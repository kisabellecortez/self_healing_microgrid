"""Decision-making layer (FS-19/FS-20/FS-21, NFS-9).

Per the design doc (3.7.2) and confirmed with the anomaly detection side:
this layer also absorbs grid-classification duties (FS-16/17/18) - there's
no separate classification module. One function classifies grid state,
another maps state -> switching action; the doc's Figure Y pseudocode
describes exactly this two-step shape and is mirrored here function-for-
function so the doc stays an accurate description of the real code.

Input contract (confirmed with anomaly detection):
    system_anomaly_score: float
    per load: anomaly_score (float), connected (0/1), critical (0/1)
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
    # Timestamp this load last went to connected=False. Needed for FS-10
    # staggered timing - caller resolves this from load_status history.
    # None -> unknown / never disconnected; reconnection isn't blocked on
    # missing data (fail-open, see _reconnect_eligible).
    disconnected_since: Optional[datetime] = None


# ── FS-16/17/18: grid state classification ─────────────────────────────
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
    point of the two-layer split. This layer's input, per the confirmed
    interface and the corrected 3.7.2 text, is anomaly scores plus
    connection/criticality flags.
    """
    features = {"system_anomaly_score": system_anomaly_score, "soc": soc}
    for load in loads:
        features[f"{load.load_id}_anomaly_score"] = load.anomaly_score
        features[f"{load.load_id}_connected"] = int(load.connected)
        features[f"{load.load_id}_critical"] = int(load.critical)
    return features


# ── FS-10: staggered reconnection windows for the three critical loads ──
# Not carried by the anomaly-detection interface (binary critical flag
# only) - hardcoded here since it's fixed hardware/spec knowledge, not
# something anomaly detection would ever send.
CRITICAL_RECONNECT_WINDOWS_SEC = {
    "critical_1": (0, 59),  # Fire and Life Safety
    "critical_2": (60, 3540),  # Security, 1-59 min
    "critical_3": (3600, 86400),  # Egress & Patient Care Lighting, 1-24 hr
}


def _reconnect_eligible(load: LoadSignal, now: datetime) -> bool:
    if not load.critical:
        return True  # FS-10 only defines windows for critical loads
    window = CRITICAL_RECONNECT_WINDOWS_SEC.get(load.load_id)
    if window is None or load.disconnected_since is None:
        return True  # fail-open: unknown load id or no timestamp, don't block indefinitely
    elapsed = (now - load.disconnected_since).total_seconds()
    return elapsed >= window[0]


def _target(load: LoadSignal, should_be_connected: bool, now: datetime) -> Action:
    if should_be_connected:
        if load.connected:
            return Action.HOLD
        return Action.RECONNECT if _reconnect_eligible(load, now) else Action.HOLD
    return Action.SHED if load.connected else Action.HOLD


# ── FS-19/FS-21: state -> action rule table (Figure Y in the design doc) ─
# Each state's action is computed fresh from current load status, not
# assumed prior state, since classify_grid_state() can jump straight from
# any state to any other in one cycle (e.g. sudden catastrophic fault:
# normal -> islanded with nothing in between).


def _reconnect_all(loads: list[LoadSignal], now: datetime) -> dict[str, Action]:
    return {l.load_id: _target(l, True, now) for l in loads}


def _shed_non_critical(loads: list[LoadSignal], now: datetime) -> dict[str, Action]:
    # Criticals stay connected/reconnect; only non-criticals shed (FS-9).
    return {l.load_id: _target(l, l.critical, now) for l in loads}


def _shed_all(loads: list[LoadSignal]) -> dict[str, Action]:
    return {l.load_id: (Action.SHED if l.connected else Action.HOLD) for l in loads}


def _shed_critical_loads_2_and_3(loads: list[LoadSignal], now: datetime) -> dict[str, Action]:
    def keep_connected(l: LoadSignal) -> bool:
        return l.critical and l.load_id not in ("critical_2", "critical_3")

    return {l.load_id: _target(l, keep_connected(l), now) for l in loads}


# Islanded + SOC >= 20%: same outcome as "shed non-critical" (criticals up,
# non-criticals down) - kept as a separate name for 1:1 traceability to
# Figure Y in the doc, even though the logic is identical.
_maintain_critical_loads = _shed_non_critical


def map_state_to_action(
    grid_state: GridState, soc: float, loads: list[LoadSignal], now: Optional[datetime] = None
) -> tuple[str, dict[str, Action]]:
    """Returns (branch_name, {load_id: Action}). branch_name matches the
    function names in Figure Y for direct doc <-> code traceability."""
    now = now or datetime.now(timezone.utc)

    if grid_state in (GridState.NORMAL, GridState.WARNING):
        return "reconnect_all", _reconnect_all(loads, now)
    if grid_state in (GridState.CRITICAL, GridState.FAULT_IMMINENT):
        return "shed_non_critical", _shed_non_critical(loads, now)

    # grid_state == ISLANDED - FS-4/FS-5 SOC thresholds take priority
    if soc < 0.10:
        return "shed_all", _shed_all(loads)
    if soc < 0.20:
        return "shed_critical_loads_2_and_3", _shed_critical_loads_2_and_3(loads, now)
    return "maintain_critical_loads", _maintain_critical_loads(loads, now)


def determine_action(
    system_anomaly_score: float, loads: list[LoadSignal], soc: float, now: Optional[datetime] = None
) -> tuple[GridState, str, dict[str, Action], dict]:
    """Top-level entry point matching determine_action() in Figure Y.
    Returns (grid_state, branch_name, per_load_actions, features) - the
    caller logs all four to the decisions table (see log_decision below).
    """
    now = now or datetime.now(timezone.utc)
    features = build_feature_vector(system_anomaly_score, loads, soc)
    grid_state = classify_grid_state(features)
    branch, actions = map_state_to_action(grid_state, soc, loads, now)
    return grid_state, branch, actions, features


# ── FS-20: log every decision with the feature vector that produced it ──


async def log_decision(db, *, grid_state: GridState, branch: str, actions: dict[str, Action],
                        features: dict, latency_ms: float, outcome: Optional[str] = None) -> None:
    from models import Decision  # local import, only needed here

    db.add(Decision(
        grid_state=grid_state,
        action=branch,
        load_actions={k: v.value for k, v in actions.items()},
        features=features,
        latency_ms=latency_ms,
        outcome=outcome,
    ))
    await db.commit()


# ── FS-10 support: resolving disconnected_since from load_status history ──
#
# LoadSignal.disconnected_since has to come from somewhere. Nothing before
# this point populates it - callers were expected to resolve it themselves,
# which meant the FS-10 staggered-timing path had only ever been exercised
# against hand-built test data, never against real logged history. This
# closes that gap.


async def _last_disconnected_at(db, load_id: str) -> Optional[datetime]:
    """Timestamp this load most recently transitioned to connected=False.
    Returns None if the load is currently connected (nothing to resolve)."""
    from sqlalchemy import text

    result = await db.execute(text("""
        SELECT time FROM load_status
        WHERE load_id = :load_id AND connected = false
          AND time > COALESCE(
              (SELECT MAX(time) FROM load_status WHERE load_id = :load_id AND connected = true),
              '-infinity'
          )
        ORDER BY time ASC
        LIMIT 1
    """), {"load_id": load_id})
    row = result.first()
    return row[0] if row else None


async def resolve_load_signals(
    db, anomaly_scores: dict[str, float], connected: dict[str, bool], critical: dict[str, bool]
) -> list[LoadSignal]:
    """Builds LoadSignal objects ready for determine_action(), resolving
    disconnected_since from load_status for every currently-disconnected
    load. Currently-connected loads get disconnected_since=None.

    Usage:
        loads = await resolve_load_signals(db, anomaly_scores, connected, critical)
        grid_state, branch, actions, features = determine_action(system_score, loads, soc)
    """
    signals = []
    for load_id, is_connected in connected.items():
        disconnected_since = None if is_connected else await _last_disconnected_at(db, load_id)
        signals.append(LoadSignal(
            load_id=load_id,
            anomaly_score=anomaly_scores[load_id],
            connected=is_connected,
            critical=critical[load_id],
            disconnected_since=disconnected_since,
        ))
    return signals
