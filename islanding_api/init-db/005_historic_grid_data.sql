-- 3.7.1's retraining table, per your teammate's proposed schema. Replaces
-- the placeholder historic_grid_data reference in
-- retrain_isolation_forests.py's fetch_training_samples(), which previously
-- raised NotImplementedError because no table backed it - see NEXT_STEPS.md.
--
-- Distinct from feature_readings (001_schema.sql): feature_readings is the
-- generic FS-11 raw ingestion log (any node, JSONB environment, includes
-- grid-wide frequency/soc that don't belong on a per-load training row).
-- historic_grid_data is purpose-built and flattened for 3.7.1's retraining
-- query - one row per (load, request), weather as queryable flat columns,
-- and a `state` flag so disconnected samples can be filtered out at query
-- time per Section 3.7.1's retraining description ("load state indicates
-- that the load is connected").
--
-- power/voltage_deviation/current_deviation are written by main.py's
-- /api/islanding using node_data ratings at write time, for convenience and
-- audit - retrain_isolation_forests.py still recomputes them from raw
-- voltage/current against CURRENT node_data ratings at train time rather
-- than trusting these, so a later node_data correction doesn't leave stale
-- deviations baked into old training rows. NULL on disconnected rows -
-- deviation from a rating is meaningless with no current flowing.
--
-- One naming deviation from the proposed column list: `time`, not
-- `timestamp` - every other hypertable in this schema (feature_readings,
-- anomaly_scores, grid_states, decisions, battery_status, load_status)
-- partitions on a column literally called `time`; this keeps that
-- consistent instead of being the one exception.
--
-- Run manually against an already-initialized DB:
--   docker exec -i aegis_postgres psql -U aegis -d aegis < init-db/005_historic_grid_data.sql

CREATE TABLE IF NOT EXISTS historic_grid_data (
    time               TIMESTAMPTZ       NOT NULL DEFAULT now(),
    load_id            INTEGER           NOT NULL REFERENCES node_data(load_id),
    voltage            DOUBLE PRECISION  NOT NULL,
    current            DOUBLE PRECISION  NOT NULL,
    power              DOUBLE PRECISION,
    voltage_deviation  DOUBLE PRECISION,
    current_deviation  DOUBLE PRECISION,
    temperature        DOUBLE PRECISION  NOT NULL,
    humidity           DOUBLE PRECISION  NOT NULL,
    wind_speed         DOUBLE PRECISION  NOT NULL,
    rainfall           DOUBLE PRECISION  NOT NULL,
    state              BOOLEAN           NOT NULL,
    PRIMARY KEY (time, load_id)
);
SELECT create_hypertable('historic_grid_data', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS historic_grid_data_load_id_time_idx ON historic_grid_data (load_id, time DESC);

COMMENT ON TABLE historic_grid_data IS
  'Per-load electrical + weather history for 3.7.1 Isolation Forest retraining. One row per load per /api/islanding request.';
COMMENT ON COLUMN historic_grid_data.state IS
  'Connected (true) / disconnected (false) - NOT the grid fault state. retrain_isolation_forests.fetch_training_samples() additionally excludes rows recorded while the grid_states classification was critical/fault_imminent/islanded, per Section 3.7.1''s "fault status is false" training filter - a load can be connected and mid-fault at the same time, so this column alone is not a sufficient filter.';
