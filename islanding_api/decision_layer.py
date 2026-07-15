"""Decision-making layer (FS-18/FS-19/FS-20, NFS-8).

Per the design doc (3.7.2) and confirmed with the anomaly detection side:
this layer also absorbs grid-classification duties (FS-15/16/17) - there's
no separate classification module. One function classifies grid state,
another maps state -> switching action; the doc's Figure Y mirrors this
two-step shape function-for-function so the doc stays an accurate
description of the real code.

Input contract (confirmed with anomaly detection):
    system_anomaly_score: float
    per load: anomaly_score (float), connected (0/1), critical (0/1)

FS/NFS numbers below match the July 12 doc revision. FS-5/FS-9/FS-10 were
rewritten in that revision (SOC-or-time staged critical shedding, critical
loads no longer disconnect at islanding onset) - this file was rebuilt to
match, not just relabeled. See the shedding section below for what
actually changed and the one open question it depends on.
"""

import os

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from models import GridState

# ── Types ────────────────────────────────────────────────────────────────


class Action(str, Enum):
    HOLD = "hold"
    SHED = "shed"
    RECONNECT = "reconnect"


@dataclass
class LoadSignal:
    load_id: str
    anomaly_score: float
    connected: bool
    critical: bool


# ── FS-15/16/17: grid state classification ─────────────────────────────
#
# Auto-loads a trained Random Forest from MODEL_PATH if one exists;
# otherwise falls back to the rule-based thresholds below (per the risk
# assessment's mitigation plan). This means training a real model later
# (train_decision_model.py) and dropping the .joblib file in place
# activates it with ZERO changes to this file or anything downstream -
# map_state_to_action only ever sees a GridState either way.
#
# NOTE / open question: FS-3 separately says the physical grid-disconnect
# switch trips when fault probability crosses a threshold. Worth confirming
# in the doc that the ISLANDED bin below is defined to BE that same
# threshold, so there's one number to tune, not two thresholds that can
# drift out of sync.
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "rf_grid_state.joblib")
_rf_bundle = None
_rf_load_attempted = False

_STATE_THRESHOLDS = (  # (upper bound exclusive, state)
    (0.2, GridState.NORMAL),
    (0.4, GridState.WARNING),
    (0.6, GridState.CRITICAL),
    (0.8, GridState.FAULT_IMMINENT),
)  # score >= 0.8 -> ISLANDED


def _load_rf():
    global _rf_bundle, _rf_load_attempted
    if not _rf_load_attempted:
        _rf_load_attempted = True
        if os.path.exists(MODEL_PATH):
            import joblib
            _rf_bundle = joblib.load(MODEL_PATH)
    return _rf_bundle


def _classify_with_thresholds(features: dict) -> GridState:
    score = features["system_anomaly_score"]
    for upper, state in _STATE_THRESHOLDS:
        if score < upper:
            return state
    return GridState.ISLANDED


def classify_grid_state(features: dict) -> GridState:
    bundle = _load_rf()
    if bundle is not None:
        import pandas as pd
        row = pd.DataFrame([[features.get(name, 0.0) for name in bundle["feature_names"]]],
                            columns=bundle["feature_names"])
        return GridState(bundle["model"].predict(row)[0])
    return _classify_with_thresholds(features)


def build_feature_vector(system_anomaly_score: float, loads: list[LoadSignal], soc: float) -> dict:
    """Feature vector for grid state classification.

    Deliberately excludes raw per-node voltage/current. Section 3.7.1
    already consumes raw voltage/current/power against node thresholds
    (isolation forest) and outputs an anomaly score - that's the whole
    point of the two-layer split. This layer's input is anomaly scores
    plus connection/criticality flags.
    """
    features = {"system_anomaly_score": system_anomaly_score, "soc": soc}
    for load in loads:
        features[f"{load.load_id}_anomaly_score"] = load.anomaly_score
        features[f"{load.load_id}_connected"] = int(load.connected)
        features[f"{load.load_id}_critical"] = int(load.critical)
    return features


def _target(load: LoadSignal, should_be_connected: bool) -> Action:
    if should_be_connected:
        return Action.HOLD if load.connected else Action.RECONNECT
    return Action.SHED if load.connected else Action.HOLD


# ── FS-9: non-critical shedding, unchanged in the rewrite ──────────────
# New FS-9 ties this to islanding specifically ("when islanding is
# triggered"), but doing it proactively in CRITICAL/FAULT_IMMINENT too
# doesn't violate that (it's a minimum, not a ceiling), and it's what 4.3
# calls out as the "predictive, not reactive" novelty claim. Left as-is.

def _reconnect_all(loads: list[LoadSignal]) -> dict[str, Action]:
    return {l.load_id: _target(l, True) for l in loads}


def _shed_non_critical(loads: list[LoadSignal]) -> dict[str, Action]:
    return {l.load_id: _target(l, l.critical) for l in loads}


# ── FS-5/FS-10: staged critical load shedding (REBUILT, not relabeled) ──
#
# Old model (previous doc revision): once islanded, all-or-nothing SOC
# tiers - shed critical_2+3 together below 20%, shed everything below 10%.
# Critical loads disconnected at islanding onset and reconnected on a
# staggered timer.
#
# New model (this revision): critical loads stay connected at islanding
# onset (FS-9) and shed individually, least-important-first, each gated by
# its own SOC-or-elapsed-time trigger, whichever comes first (FS-5/FS-10).
# Reconnection is no longer staggered - FS-10 now means something
# different (shedding stages, not reconnect windows), and no other spec
# defines reconnect timing, so reconnect_all() below is unconditional.
#
# OPEN QUESTION, RESOLVED as an implementation choice: FS-4 ties the 10%
# SOC threshold to a BATTERY TRANSFER (Battery 1 -> Battery 2), not a load
# shed, and FS-5 only defines shed thresholds for critical_2 and
# critical_3. Section 3.2.2 says "Critical Load 1 remains connected until
# the SOC shutdown threshold refined in FS-5 is reached" - but FS-5 never
# actually defines one for critical_1. Resolved below: critical_1 sheds on
# SOC alone, no time trigger, at the same 10%
# threshold FS-4 already uses to justify the Battery 1 -> Battery 2
# transfer ("discharging below 10% can cause irreversible capacity
# degradation," 3.2.1). Rationale:
#   - Reuses an SOC floor the doc already justifies, rather than inventing
#     a new number with no grounding.
#   - No time trigger: shedding Fire/Life Safety because a clock expired,
#     independent of whether the battery is actually at risk, doesn't
#     hold up the way it does for critical_2/3 (whose time triggers exist
#     specifically to make the demo practical, not because time itself
#     endangers anything). float("inf") disables the time condition
#     without touching _should_shed's shared OR logic.
#   - `soc` here is deliberately not battery-specific - it already means
#     "whichever battery is currently active" for critical_2/3 (matching
#     the Simulink model's Active_SOC), so this extends the same
#     convention rather than introducing battery-tracking logic. In
#     practice this means critical_1 only sheds once Battery 2 also hits
#     10%, since Battery 1 transfers away at 10% before ever going lower.
CRITICAL_SHED_TRIGGERS = {
    "critical_3": {"soc_below": 0.20, "islanded_longer_than_sec": 59},         # 59 sec
    "critical_2": {"soc_below": 0.15, "islanded_longer_than_sec": 59 * 60},    # 59 min
    "critical_1": {"soc_below": 0.10, "islanded_longer_than_sec": float("inf")},  # SOC only, mirrors FS-4
}


def _should_shed(load: LoadSignal, soc: float, time_islanded_sec: float) -> bool:
    trigger = CRITICAL_SHED_TRIGGERS.get(load.load_id)
    if trigger is None:
        return False
    return soc < trigger["soc_below"] or time_islanded_sec > trigger["islanded_longer_than_sec"]


def _staged_critical_shedding(loads: list[LoadSignal], soc: float, time_islanded_sec: float) -> dict[str, Action]:
    actions = {}
    for load in loads:
        if not load.critical:
            actions[load.load_id] = _target(load, False)  # non-critical stays shed while islanded
        else:
            actions[load.load_id] = _target(load, not _should_shed(load, soc, time_islanded_sec))
    return actions


def map_state_to_action(
    grid_state: GridState, soc: float, loads: list[LoadSignal], time_islanded_sec: float = 0.0
) -> tuple[str, dict[str, Action]]:
    """Returns (branch_name, {load_id: Action}). branch_name matches the
    function names in Figure Y for direct doc <-> code traceability."""
    if grid_state in (GridState.NORMAL, GridState.WARNING):
        return "reconnect_all", _reconnect_all(loads)
    if grid_state in (GridState.CRITICAL, GridState.FAULT_IMMINENT):
        return "shed_non_critical", _shed_non_critical(loads)
    # grid_state == ISLANDED - FS-5/FS-10 staged shedding takes priority over the anomaly-driven state
    return "staged_critical_shedding", _staged_critical_shedding(loads, soc, time_islanded_sec)


def determine_action(
    system_anomaly_score: float, loads: list[LoadSignal], soc: float, time_islanded_sec: float = 0.0
) -> tuple[GridState, str, dict[str, Action], dict]:
    """Top-level entry point matching determine_action() in Figure Y.
    Returns (grid_state, branch_name, per_load_actions, features) - the
    caller logs all four to the decisions table (see log_decision below).

    time_islanded_sec: seconds since the system last entered ISLANDED,
    resolved via time_since_islanding_started() before calling this - kept
    as a plain float here so this function stays pure/DB-free, same
    reasoning as everything else in this module.
    """
    features = build_feature_vector(system_anomaly_score, loads, soc)
    grid_state = classify_grid_state(features)
    branch, actions = map_state_to_action(grid_state, soc, loads, time_islanded_sec)
    return grid_state, branch, actions, features


# ── FS-19: log every decision with the feature vector that produced it ──


async def log_decision(db, *, grid_state: GridState, branch: str, actions: dict[str, Action],
                        features: dict, latency_ms: float, outcome: Optional[str] = None) -> None:
    from models import Decision, GridStateLog  # local import, only needed here

    # Also writes to grid_states, not just decisions. Previously nothing
    # populated grid_states at all, which meant time_since_islanding_started()
    # below had no history to query. fault_probability and anomaly_score are
    # set to the same value deliberately - per Section 3.5, the anomaly score
    # stands in for fault probability in the real system, there's no second
    # independent signal to store.
    db.add(GridStateLog(
        state=grid_state,
        fault_probability=features.get("system_anomaly_score"),
        anomaly_score=features.get("system_anomaly_score"),
    ))
    db.add(Decision(
        grid_state=grid_state,
        action=branch,
        load_actions={k: v.value for k, v in actions.items()},
        features=features,
        latency_ms=latency_ms,
        outcome=outcome,
    ))
    await db.commit()


def build_load_signals(
    anomaly_scores: dict[str, float], connected: dict[str, bool], critical: dict[str, bool]
) -> list[LoadSignal]:
    """Zips the raw interface data (per load_id) into LoadSignal objects
    ready for determine_action(). No DB access needed - unlike the old
    per-load reconnect-timing version, nothing here depends on history."""
    return [
        LoadSignal(load_id=lid, anomaly_score=anomaly_scores[lid], connected=connected[lid], critical=critical[lid])
        for lid in connected
    ]


# ── FS-5/FS-10 support: how long has the system been islanded? ─────────


async def time_since_islanding_started(db) -> float:
    """Seconds since the system most recently transitioned into ISLANDED.
    Returns 0.0 if not currently islanded (nothing to measure) - callers
    in a non-islanded state don't use this value anyway, since
    map_state_to_action only consults it in the ISLANDED branch."""
    from sqlalchemy import text

    result = await db.execute(text("""
        SELECT time FROM grid_states
        WHERE state = 'islanded'
          AND time > COALESCE(
              (SELECT MAX(time) FROM grid_states WHERE state != 'islanded'),
              '-infinity'
          )
        ORDER BY time ASC
        LIMIT 1
    """))
    row = result.first()
    if row is None:
        return 0.0
    return (datetime.now(timezone.utc) - row[0]).total_seconds()