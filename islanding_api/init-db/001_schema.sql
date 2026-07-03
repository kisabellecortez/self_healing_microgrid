-- AEGIS islanding_api database schema
-- Auto-run on first container start via docker-entrypoint-initdb.d (see docker-compose.yml)
-- Requires the TimescaleDB extension (bundled in the timescale/timescaledb Docker image)

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── Enum types ──────────────────────────────────────────────────────────
CREATE TYPE grid_state_enum AS ENUM ('normal', 'warning', 'critical', 'fault_imminent', 'islanded'); -- FS-17
CREATE TYPE load_type_enum  AS ENUM ('critical', 'non_critical');

-- ── FS-6 / FS-7 / FS-8 / FS-11 / FS-12: electrical features streamed from the edge MCU ──
CREATE TABLE feature_readings (
    time               TIMESTAMPTZ      NOT NULL DEFAULT now(),
    node_id            TEXT             NOT NULL,
    voltage            DOUBLE PRECISION,
    current            DOUBLE PRECISION,
    frequency          DOUBLE PRECISION,
    fault_probability  DOUBLE PRECISION,
    soc                DOUBLE PRECISION,
    season             TEXT,
    environment        JSONB,            -- FS-12: flexible contextual data (temp, weather, etc.)
    PRIMARY KEY (time, node_id)
);
SELECT create_hypertable('feature_readings', 'time');
CREATE INDEX ON feature_readings (node_id, time DESC);

-- ── FS-13 / FS-14: anomaly detection output ─────────────────────────────
CREATE TABLE anomaly_scores (
    id             BIGSERIAL         NOT NULL,
    time           TIMESTAMPTZ       NOT NULL DEFAULT now(),
    node_id        TEXT,
    anomaly_score  DOUBLE PRECISION  NOT NULL,
    model_version  TEXT,
    PRIMARY KEY (time, id)
);
SELECT create_hypertable('anomaly_scores', 'time');

-- ── FS-16 / FS-17 / FS-18: grid state classification ────────────────────
CREATE TABLE grid_states (
    id                 BIGSERIAL         NOT NULL,
    time               TIMESTAMPTZ       NOT NULL DEFAULT now(),
    state              grid_state_enum   NOT NULL,
    fault_probability  DOUBLE PRECISION,
    anomaly_score      DOUBLE PRECISION,
    PRIMARY KEY (time, id)
);
SELECT create_hypertable('grid_states', 'time');

-- ── FS-19 / FS-20 / FS-21 / NFS-9: decision-making layer ────────────────
CREATE TABLE decisions (
    id           BIGSERIAL         NOT NULL,
    time         TIMESTAMPTZ       NOT NULL DEFAULT now(),
    grid_state   grid_state_enum   NOT NULL,
    action       TEXT              NOT NULL,
    latency_ms   DOUBLE PRECISION,
    outcome      TEXT,                    -- filled in after the fact, feeds FS-20 (adaptive decisions)
    PRIMARY KEY (time, id)
);
SELECT create_hypertable('decisions', 'time');

-- ── FS-4 / FS-5: battery priority + SOC-dependent load reduction ────────
CREATE TABLE battery_status (
    time        TIMESTAMPTZ       NOT NULL DEFAULT now(),
    battery_id  TEXT              NOT NULL,
    soc         DOUBLE PRECISION  NOT NULL,
    active      BOOLEAN           NOT NULL DEFAULT false,
    PRIMARY KEY (time, battery_id)
);
SELECT create_hypertable('battery_status', 'time');

-- ── FS-9 / FS-10 / FS-22: load shedding + staggered reconnection ────────
CREATE TABLE load_status (
    time            TIMESTAMPTZ      NOT NULL DEFAULT now(),
    load_id         TEXT             NOT NULL,
    load_type       load_type_enum   NOT NULL,
    connected       BOOLEAN          NOT NULL,
    priority_level  INTEGER,
    PRIMARY KEY (time, load_id)
);
SELECT create_hypertable('load_status', 'time');
