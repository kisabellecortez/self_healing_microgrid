"""Quick sanity check that the whole decision-layer pipeline actually works:
DB connectivity, decision_layer logic across scenarios (including the
staged critical-shedding rewrite), logging to Postgres, and NFS-8 latency.
Re-run this any time after touching the schema, models.py, or
decision_layer.py to catch breakage early.

Usage:
    python smoke_test.py

Exits non-zero on any failure, so it's also CI/pre-commit friendly.
"""

import asyncio
import sys
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from database import AsyncSessionLocal
from decision_layer import (
    Action, GridState, build_load_signals, determine_action, log_decision, time_since_islanding_started,
)
from models import Decision, GridStateLog

SCENARIOS = [
    # (label, system_anomaly_score, soc, expect_grid_state)
    ("healthy grid", 0.05, 0.90, "normal"),
    ("pre-fault warning", 0.30, 0.90, "warning"),
    ("islanded, healthy battery", 0.90, 0.90, "islanded"),
]


def make_loads(connected=True):
    return build_load_signals(
        anomaly_scores={f"critical_{i}": 0.2 for i in (1, 2, 3)} | {f"noncritical_{i}": 0.2 for i in (1, 2)},
        connected={f"critical_{i}": connected for i in (1, 2, 3)} | {f"noncritical_{i}": connected for i in (1, 2)},
        critical={f"critical_{i}": True for i in (1, 2, 3)} | {f"noncritical_{i}": False for i in (1, 2)},
    )


async def main() -> bool:
    all_ok = True

    print("1. Checking database connectivity...")
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(select(1))
        print("   OK\n")
    except Exception as e:
        print(f"   FAILED: {e}")
        print("   Is `docker compose up -d` running? Does your .env match docker-compose.yml?")
        return False

    print("2. Running decision scenarios...\n")
    for label, score, soc, expected_state in SCENARIOS:
        loads = make_loads(connected=True)
        start = time.perf_counter()
        grid_state, branch, actions, features = determine_action(
            system_anomaly_score=score, loads=loads, soc=soc, time_islanded_sec=0.0
        )
        latency_ms = (time.perf_counter() - start) * 1000

        state_ok = grid_state.value == expected_state
        latency_ok = latency_ms < 100  # NFS-8

        status = "OK" if (state_ok and latency_ok) else "FAILED"
        print(f"   [{status}] {label}: state={grid_state.value} (expected {expected_state}), "
              f"branch={branch}, latency={latency_ms:.3f}ms")
        if not state_ok or not latency_ok:
            all_ok = False

        async with AsyncSessionLocal() as db:
            await log_decision(db, grid_state=grid_state, branch=branch, actions=actions,
                                features=features, latency_ms=latency_ms)

    print("\n3. Reading logged decisions back from Postgres...")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Decision).order_by(Decision.time.desc()).limit(len(SCENARIOS)))
        rows = result.scalars().all()
        print(f"   Found {len(rows)} rows (expected {len(SCENARIOS)})")
        if len(rows) != len(SCENARIOS):
            all_ok = False
        for row in rows:
            has_features = row.features is not None and len(row.features) > 0
            has_actions = row.load_actions is not None and len(row.load_actions) > 0
            if not (has_features and has_actions):
                print(f"   FAILED: row at {row.time} missing features or load_actions")
                all_ok = False

    print("\n4. Checking grid_states got populated by log_decision...")
    async with AsyncSessionLocal() as db:
        gs_rows = (await db.execute(select(GridStateLog))).scalars().all()
        gs_ok = len(gs_rows) >= len(SCENARIOS)
        print(f"   [{'OK' if gs_ok else 'FAILED'}] {len(gs_rows)} grid_states rows present (expected >= {len(SCENARIOS)})")
        all_ok = all_ok and gs_ok

    print("\n5. Checking staged critical shedding (SOC and time triggers)...")
    try:
        loads_islanded = make_loads(connected=True)

        # critical_3: SOC trigger at 20%, should shed at SOC=0.15 even at t=0
        _, _, actions_a, _ = determine_action(0.9, loads_islanded, soc=0.15, time_islanded_sec=0)
        soc_trigger_ok = actions_a["critical_3"] == Action.SHED and actions_a["critical_2"] == Action.HOLD
        print(f"   [{'OK' if soc_trigger_ok else 'FAILED'}] critical_3 sheds on SOC<20% even at t=0 "
              f"(critical_3={actions_a['critical_3'].value}, critical_2={actions_a['critical_2'].value})")

        # critical_2: time trigger at 59min, healthy SOC - should hold at 58min, shed at 60min
        _, _, actions_b, _ = determine_action(0.9, make_loads(True), soc=0.9, time_islanded_sec=58 * 60)
        _, _, actions_c, _ = determine_action(0.9, make_loads(True), soc=0.9, time_islanded_sec=60 * 60)
        time_trigger_ok = actions_b["critical_2"] == Action.HOLD and actions_c["critical_2"] == Action.SHED
        print(f"   [{'OK' if time_trigger_ok else 'FAILED'}] critical_2 held at 58min ({actions_b['critical_2'].value}), "
              f"shed at 60min ({actions_c['critical_2'].value})")

        # critical_1: SOC-only trigger at 10% - holds above it no matter how
        # long islanded, sheds below it immediately regardless of time
        _, _, actions_d, _ = determine_action(0.9, make_loads(True), soc=0.11, time_islanded_sec=999999)
        _, _, actions_e, _ = determine_action(0.9, make_loads(True), soc=0.09, time_islanded_sec=0)
        c1_ok = actions_d["critical_1"] == Action.HOLD and actions_e["critical_1"] == Action.SHED
        print(f"   [{'OK' if c1_ok else 'FAILED'}] critical_1: holds at 11% after 999999s "
              f"({actions_d['critical_1'].value}), sheds at 9% immediately ({actions_e['critical_1'].value})")

        if not (soc_trigger_ok and time_trigger_ok and c1_ok):
            all_ok = False
    except Exception as e:
        print(f"   FAILED: {e}")
        all_ok = False

    print("\n6. Checking time_since_islanding_started resolves from real history...")
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as db:
            # Isolate from steps 2-4, which already wrote their own grid_states
            # rows moments ago (including a fresh ISLANDED one) - without this,
            # the resolver correctly finds that more recent streak instead of
            # the one seeded below, which isn't a resolver bug, just test setup.
            await db.execute(text("TRUNCATE grid_states"))
            db.add(GridStateLog(time=datetime.now(timezone.utc) - timedelta(seconds=200), state=GridState.NORMAL))
            db.add(GridStateLog(time=datetime.now(timezone.utc) - timedelta(seconds=90), state=GridState.ISLANDED))
            await db.commit()
        async with AsyncSessionLocal() as db:
            resolved = await time_since_islanding_started(db)
        resolve_ok = 85 < resolved < 95
        print(f"   [{'OK' if resolve_ok else 'FAILED'}] resolved {resolved:.1f}s (expected ~90s)")
        all_ok = all_ok and resolve_ok
    except Exception as e:
        print(f"   FAILED: {e}")
        all_ok = False

    return all_ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED - see above"))
    sys.exit(0 if ok else 1)