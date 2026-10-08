-- Free-tier gateway (ADR 0007): capabilities served only by zero-priced pools carry
-- cost_mode 'zero' (CostMode.ZERO). The 0001 check predates it, so `pitwall init`
-- with the gateway seed failed on capabilities_cost_mode_check.

ALTER TABLE pitwall.capabilities DROP CONSTRAINT IF EXISTS capabilities_cost_mode_check;
ALTER TABLE pitwall.capabilities ADD CONSTRAINT capabilities_cost_mode_check CHECK (
  cost_mode IN ('per_second', 'per_request', 'per_token', 'zero')
);
