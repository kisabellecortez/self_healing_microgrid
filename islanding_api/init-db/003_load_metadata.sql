-- SUPERSEDED by 004_node_data.sql, which renames this table to node_data
-- (matching the schema your teammate proposed) and adds power_rating.
-- Left in place, unedited, since this may already have been applied to a
-- running database - 004 migrates it forward rather than replacing it here.
-- Do not apply this file to a fresh database on its own; run through 004.
--
-- Per-load reference data, needed by both ML layers (3.7.1 and 3.7.2) and
-- previously missing from the schema entirely:
--   - anomaly_detection.py needs rated_voltage/rated_current per load to
--     compute voltage_deviation/current_deviation (Section 3.7.1 feature vector).
--   - decision_layer.py's LoadSignal needs a `critical` flag and a stable
--     string id ("critical_1", "noncritical_1", ...) - CRITICAL_SHED_TRIGGERS
--     in decision_layer.py is keyed on exactly these strings.
-- The embedded system's JSON payload (Figure 6) identifies loads by a small
-- integer load_id. This table is the single place that maps that integer to
-- the decision layer's semantic name, so both layers agree on which load is
-- which without duplicating the mapping in Python on either side.
--
-- Run manually against an already-initialized DB (init scripts only run
-- once, against an empty volume):
--   docker exec -i aegis_postgres psql -U aegis -d aegis < init-db/003_load_metadata.sql

CREATE TABLE IF NOT EXISTS load_metadata (
    load_id         INTEGER           PRIMARY KEY,           -- matches JSON payload's numeric load_id
    name            TEXT              NOT NULL UNIQUE,        -- decision_layer.py LoadSignal.load_id / CRITICAL_SHED_TRIGGERS key
    load_type       load_type_enum    NOT NULL,
    rated_voltage   DOUBLE PRECISION  NOT NULL,
    rated_current   DOUBLE PRECISION  NOT NULL,
    priority_level  INTEGER
);

COMMENT ON TABLE load_metadata IS
  'Static per-load reference data (Section 3.4). rated_current values marked PLACEHOLDER below are order-of-magnitude estimates, not datasheet/measured values - replace before training on real data.';
COMMENT ON COLUMN load_metadata.load_id IS
  'Numeric id as sent by the embedded system in the JSON payload (Figure 6, Section 3.6).';
COMMENT ON COLUMN load_metadata.name IS
  'String id used throughout decision_layer.py (LoadSignal.load_id, CRITICAL_SHED_TRIGGERS keys, build_feature_vector column names).';

-- Section 3.4: five loads, all on the 12V DC bus (per Table 3.3.7 TP_MainGrid/TP_Batt1/TP_Batt2 = 12V).
INSERT INTO load_metadata (load_id, name, load_type, rated_voltage, rated_current, priority_level) VALUES
    -- Critical Load 1 - Fire and Life Safety - DFRobot FIT0441 12V brushless DC motor, 159 RPM.
    -- rated_current is a PLACEHOLDER (order-of-magnitude only) - replace with the FIT0441 datasheet
    -- or bench-measured running current before training on real data.
    (1, 'critical_1',    'critical',     12.0, 0.30, 1),
    -- Critical Load 2 - Security - Delta Electronics DSOL-1351-12C solenoid, rated 11W at 12V.
    -- rated_current derived directly from the doc's stated wattage: 11W / 12V.
    (2, 'critical_2',    'critical',     12.0, 0.917, 2),
    -- Critical Load 3 - Egress & Patient Care Lighting - LED module.
    -- rated_current is a PLACEHOLDER - replace with the actual LED module's measured current.
    (3, 'critical_3',    'critical',     12.0, 0.20, 3),
    -- Non-critical Load 1 - HVAC - same FIT0441 motor as Critical Load 1. Same placeholder caveat.
    (4, 'noncritical_1', 'non_critical', 12.0, 0.30, 4),
    -- Non-critical Load 2 - General Lighting - LED module. Same placeholder caveat as Critical Load 3.
    (5, 'noncritical_2', 'non_critical', 12.0, 0.20, 5)
ON CONFLICT (load_id) DO NOTHING;
