-- 0024: persist lease readiness, proxy activity, idle policy, and price cap.

ALTER TABLE pitwall.leases
  ADD COLUMN last_traffic_at TIMESTAMPTZ,
  ADD COLUMN ready_at TIMESTAMPTZ,
  ADD COLUMN idle_timeout_min INTEGER
    CHECK (idle_timeout_min IS NULL OR idle_timeout_min >= 5),
  ADD COLUMN max_usd_per_hour NUMERIC(12,4)
    CHECK (max_usd_per_hour IS NULL OR max_usd_per_hour > 0);

CREATE INDEX idx_leases_idle
  ON pitwall.leases(state, last_traffic_at)
  WHERE idle_timeout_min IS NOT NULL;
