-- Supersedes init-db/003_load_metadata.sql. Your teammate's proposed schema
-- for 3.7.1's retraining table (historic_grid_data, see 005) included a
-- node_data table describing the exact same 5 rows load_metadata already
-- covered (per-load rating data). Rather than keep two tables that can
-- silently drift out of sync on the same ratings, this migration renames
-- load_metadata to node_data and adopts her column names
-- (voltage_rating/current_rating/power_rating), while keeping the two
-- columns 3.7.2 depends on that weren't in her screenshot:
--   - name: the string id decision_layer.py runs on (LoadSignal.load_id,
--     CRITICAL_SHED_TRIGGERS keys) - load_id alone (int) doesn't map to
--     those without this.
--   - load_type: the critical/non-critical flag map_state_to_action's
--     shedding logic branches on.
--
-- Written to apply cleanly whether or not 003 was ever run against this
-- database - see the two branches below.
--
-- Run manually against an already-initialized DB:
--   docker exec -i aegis_postgres psql -U aegis -d aegis < init-db/004_node_data.sql

-- Branch 1: 003 already ran (load_metadata exists) - migrate it in place.
ALTER TABLE IF EXISTS load_metadata RENAME TO node_data;
ALTER TABLE IF EXISTS node_data RENAME COLUMN rated_voltage TO voltage_rating;
ALTER TABLE IF EXISTS node_data RENAME COLUMN rated_current TO current_rating;
ALTER TABLE IF EXISTS node_data ADD COLUMN IF NOT EXISTS power_rating DOUBLE PRECISION;
-- V * I is exact for this DC bus (Section 3.4) - if node_data ever needs to
-- describe an AC load, this backfill is wrong and power_rating needs a
-- power factor applied, not a bare product.
UPDATE node_data SET power_rating = voltage_rating * current_rating WHERE power_rating IS NULL;
ALTER TABLE node_data ALTER COLUMN power_rating SET NOT NULL;

-- Branch 2: 003 never ran (fresh database) - create node_data directly with
-- the full column set. No-ops if branch 1 already created the table above.
CREATE TABLE IF NOT EXISTS node_data (
    load_id         INTEGER           PRIMARY KEY,
    name            TEXT              NOT NULL UNIQUE,
    load_type       load_type_enum    NOT NULL,
    voltage_rating  DOUBLE PRECISION  NOT NULL,
    current_rating  DOUBLE PRECISION  NOT NULL,
    power_rating    DOUBLE PRECISION  NOT NULL,
    priority_level  INTEGER
);

COMMENT ON TABLE node_data IS
  'Static per-load reference data (Section 3.4). Shared by 3.7.1 (voltage_rating/current_rating/power_rating) and 3.7.2 (name, load_type). Non-solenoid current_rating values are still PLACEHOLDER estimates - see NEXT_STEPS.md.';
COMMENT ON COLUMN node_data.load_id IS
  'Numeric id as sent by the embedded system in the JSON payload (Figure 6, Section 3.6).';
COMMENT ON COLUMN node_data.name IS
  'String id used throughout decision_layer.py (LoadSignal.load_id, CRITICAL_SHED_TRIGGERS keys, build_feature_vector column names).';

-- Seed only if the table is empty - safe whether this is a fresh CREATE or
-- a migrated-in-place RENAME (which already carries 003's seeded rows).
INSERT INTO node_data (load_id, name, load_type, voltage_rating, current_rating, power_rating, priority_level)
SELECT * FROM (VALUES
    (1, 'critical_1',    'critical'::load_type_enum,     12.0, 0.30,  3.60, 1),
    (2, 'critical_2',    'critical'::load_type_enum,     12.0, 0.917, 11.0, 2),
    (3, 'critical_3',    'critical'::load_type_enum,     12.0, 0.20,  2.40, 3),
    (4, 'noncritical_1', 'non_critical'::load_type_enum, 12.0, 0.30,  3.60, 4),
    (5, 'noncritical_2', 'non_critical'::load_type_enum, 12.0, 0.20,  2.40, 5)
) AS seed(load_id, name, load_type, voltage_rating, current_rating, power_rating, priority_level)
WHERE NOT EXISTS (SELECT 1 FROM node_data);
