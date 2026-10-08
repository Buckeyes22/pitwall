-- ROUTE-01: retain the deterministic, safe production route decision used by
-- every workload surface. The document contains provider/cost/score metadata,
-- never the request payload or credential values.

ALTER TABLE pitwall.workloads
  ADD COLUMN route_plan_id TEXT,
  ADD COLUMN route_plan JSONB,
  ADD COLUMN route_attempts JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE pitwall.workloads
  ADD CONSTRAINT workloads_route_plan_pair_check CHECK (
    (route_plan_id IS NULL AND route_plan IS NULL)
    OR (route_plan_id IS NOT NULL AND route_plan IS NOT NULL)
  ),
  ADD CONSTRAINT workloads_route_plan_object_check CHECK (
    route_plan IS NULL OR jsonb_typeof(route_plan) = 'object'
  ),
  ADD CONSTRAINT workloads_route_plan_id_shape_check CHECK (
    route_plan_id IS NULL OR route_plan_id ~ '^plan_[0-9a-f]{32}$'
  ),
  ADD CONSTRAINT workloads_route_plan_identity_check CHECK (
    route_plan IS NULL OR (
      route_plan ? 'plan_id'
      AND route_plan ->> 'plan_id' IS NOT NULL
      AND route_plan ->> 'plan_id' = route_plan_id
    )
  ),
  ADD CONSTRAINT workloads_route_plan_size_check CHECK (
    route_plan IS NULL OR pg_column_size(route_plan) <= 1048576
  ),
  ADD CONSTRAINT workloads_route_attempts_shape_check CHECK (
    jsonb_typeof(route_attempts) = 'array'
    AND jsonb_array_length(route_attempts) <= 100
  ),
  ADD CONSTRAINT workloads_route_attempts_size_check CHECK (
    pg_column_size(route_attempts) <= 1048576
  );

CREATE INDEX idx_workloads_route_plan_id
  ON pitwall.workloads(route_plan_id)
  WHERE route_plan_id IS NOT NULL;
