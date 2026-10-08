-- Runtime budget limits: one audited row that overrides PITWALL_MONTHLY_BUDGET_USD and
-- PITWALL_PER_REQUEST_MAX_USD without a restart. No row means the environment values apply.

CREATE TABLE pitwall.budget_limits (
  id                   SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  monthly_budget_usd   NUMERIC(14, 6) NOT NULL CHECK (monthly_budget_usd > 0),
  per_request_max_usd  NUMERIC(14, 6) NOT NULL CHECK (per_request_max_usd > 0),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by           TEXT NOT NULL CHECK (length(btrim(updated_by)) > 0),
  reason               TEXT NOT NULL CHECK (length(btrim(reason)) > 0)
);

-- Every budget-limit change writes a config_audit row
-- (entity_type 'budget_limits', action 'budget_limits.set') through insert_audit,
-- and the surfaces that change limits record their own actors. Extend the
-- allow-lists the way 0021 and 0027 did; the audit contract test holds the
-- schema and the call sites together.

ALTER TABLE pitwall.config_audit
  DROP CONSTRAINT IF EXISTS config_audit_entity_type_check;
ALTER TABLE pitwall.config_audit
  ADD CONSTRAINT config_audit_entity_type_check CHECK (
    entity_type IN (
      'capability', 'provider', 'volume', 'template', 'drill', 'lease',
      'webhook_subscription', 'retention_run', 'budget_limits'
    )
  );

ALTER TABLE pitwall.config_audit
  DROP CONSTRAINT IF EXISTS config_audit_action_check;
ALTER TABLE pitwall.config_audit
  ADD CONSTRAINT config_audit_action_check CHECK (
    action IN (
      'create', 'update', 'delete', 'enable', 'disable', 'hibernate',
      'patch', 'renew', 'rotate', 'deactivate', 'activate', 'archive', 'purge',
      'stop', 'lease_ready', 'lease_closed', 'budget_limits.set'
    )
  );

ALTER TABLE pitwall.config_audit
  DROP CONSTRAINT IF EXISTS config_audit_actor_check;
ALTER TABLE pitwall.config_audit
  ADD CONSTRAINT config_audit_actor_check CHECK (
    actor IN (
      'rest:admin', 'mcp:session-id', 'mcp:admin', 'rest:lease', 'rest:webhook',
      'mcp', 'system',
      'system:lease', 'system:lease-controller',
      'api:admin', 'cli'
    )
  );
