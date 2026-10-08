-- RP-01: preserve the exact workload-to-provider-resource identity required
-- for authoritative Pod billing truth-up. Historical leases remain unmapped;
-- no identity is inferred from names, timestamps, or provider-wide balances.

ALTER TABLE pitwall.leases
  ADD COLUMN workload_id TEXT REFERENCES pitwall.workloads(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX idx_leases_workload_billing_identity
  ON pitwall.leases(workload_id)
  WHERE workload_id IS NOT NULL;

CREATE UNIQUE INDEX idx_leases_provider_resource_billing_identity
  ON pitwall.leases(provider_id, external_resource_id)
  WHERE workload_id IS NOT NULL
    AND external_resource_id IS NOT NULL;

CREATE INDEX idx_leases_pending_actual_cost
  ON pitwall.leases(terminated_at, provider_id)
  WHERE workload_id IS NOT NULL
    AND terminated_at IS NOT NULL;
