-- Adds what FS-20 actually requires: "every control action is logged...
-- alongside the feature vector that produced it" (Design Doc, 3.7.2).
-- The original decisions table only logged the resulting action, not the
-- inputs - can't retrain on that.
--
-- Run manually against an already-initialized DB (init scripts only run
-- once, against an empty volume):
--   docker exec -i aegis_postgres psql -U aegis -d aegis < init-db/002_decisions_features.sql

ALTER TABLE decisions ADD COLUMN IF NOT EXISTS features JSONB;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS load_actions JSONB;

COMMENT ON COLUMN decisions.action IS
  'Which rule branch fired - matches the function names in Figure Y of the design doc (reconnect_all, shed_non_critical, shed_all, shed_critical_loads_2_and_3, maintain_critical_loads)';
COMMENT ON COLUMN decisions.load_actions IS
  'Per-load result of that branch, e.g. {"critical_1": "reconnect", "noncritical_1": "shed"} - what the Switch Control Output Interface actually consumes';
COMMENT ON COLUMN decisions.features IS
  'Feature vector that produced grid_state - system + per-load anomaly scores, connected/critical flags, soc. Needed to retrain per FS-20.';
