"""Quick sanity check that the whole decision-layer pipeline actually works:
DB connectivity, decision_layer logic across a few scenarios, logging to
Postgres, and NFS-9 latency. Re-run this any time after touching the
schema, models.py, or decision_layer.py to catch breakage early.

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
from decision_layer import LoadSignal, determine_action, log_decision, resolve_load_signals, map_state_to_action, Action
from models import Decision, LoadStatus, LoadType, GridState

SCENARIOS = [
    # (label, system_anomaly_score, soc, expect_grid_state)
    ("healthy grid", 0.05, 0.90, "normal"),
    ("pre-fault warning", 0.30, 0.90, "warning"),
    ("islanded, healthy battery", 0.90, 0.50, "islanded"),
    ("islanded, low battery", 0.90, 0.15, "islanded"),
    ("islanded, critical battery", 0.90, 0.05, "islanded"),
]


def make_loads():
    return (
        [LoadSignal(f"critical_{i}", anomaly_score=0.2, connected=True, critical=True) for i in (1, 2, 3)]
        + [LoadSignal(f"noncritical_{i}", anomaly_score=0.2, connected=True, critical=False) for i in (1, 2)]
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
        loads = make_loads()
        start = time.perf_counter()
        grid_state, branch, actions, features = determine_action(
            system_anomaly_score=score, loads=loads, soc=soc
        )
        latency_ms = (time.perf_counter() - start) * 1000

        state_ok = grid_state.value == expected_state
        latency_ok = latency_ms < 100  # NFS-9

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

    print("\n4. Checking FS-10 reconnection timing (resolve_load_signals)...")
    try:
        async with AsyncSessionLocal() as db:
            # critical_2 "disconnected" 20s ago - inside its 60s window, must stay held
            db.add(LoadStatus(time=datetime.now(timezone.utc) - timedelta(seconds=20), load_id="critical_2",
                               load_type=LoadType.CRITICAL, connected=False, priority_level=2))
            # critical_3 "disconnected" 2 hours ago - past its 1hr window, must be eligible
            db.add(LoadStatus(time=datetime.now(timezone.utc) - timedelta(hours=2), load_id="critical_3",
                               load_type=LoadType.CRITICAL, connected=False, priority_level=3))
            await db.commit()

        async with AsyncSessionLocal() as db:
            loads = await resolve_load_signals(
                db,
                anomaly_scores={"critical_2": 0.1, "critical_3": 0.1},
                connected={"critical_2": False, "critical_3": False},
                critical={"critical_2": True, "critical_3": True},
            )
            _, actions = map_state_to_action(GridState.NORMAL, soc=0.9, loads=loads)

        held_ok = actions["critical_2"] == Action.HOLD
        reconnect_ok = actions["critical_3"] == Action.RECONNECT
        print(f"   [{'OK' if held_ok else 'FAILED'}] critical_2 (disconnected 20s ago, 60s window): "
              f"action={actions['critical_2'].value} (expected hold)")
        print(f"   [{'OK' if reconnect_ok else 'FAILED'}] critical_3 (disconnected 2h ago, 1h window): "
              f"action={actions['critical_3'].value} (expected reconnect)")
        if not (held_ok and reconnect_ok):
            all_ok = False
    except Exception as e:
        print(f"   FAILED: {e}")
        all_ok = False

    return all_ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED - see above"))
    sys.exit(0 if ok else 1)
