-- 0027: admit the lease lifecycle actors and actions the serve path records.

-- Arming and disarming a serve provider, and stopping a lease, write audit rows
-- through insert_audit. Those actors and actions were introduced without
-- extending these constraints, so every such write failed against a real
-- database while unit tests, which drive the path through fakes, stayed green.

ALTER TABLE pitwall.config_audit
  DROP CONSTRAINT IF EXISTS config_audit_action_check;
ALTER TABLE pitwall.config_audit
  ADD CONSTRAINT config_audit_action_check CHECK (
    action IN (
      'create', 'update', 'delete', 'enable', 'disable', 'hibernate',
      'patch', 'renew', 'rotate', 'deactivate', 'activate', 'archive', 'purge',
      'stop', 'lease_ready', 'lease_closed'
    )
  );

ALTER TABLE pitwall.config_audit
  DROP CONSTRAINT IF EXISTS config_audit_actor_check;
ALTER TABLE pitwall.config_audit
  ADD CONSTRAINT config_audit_actor_check CHECK (
    actor IN (
      'rest:admin', 'mcp:session-id', 'mcp:admin', 'rest:lease', 'rest:webhook',
      'mcp', 'system',
      'system:lease', 'system:lease-controller'
    )
  );
