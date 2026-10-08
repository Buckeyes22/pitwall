-- Lease renewals from the CLI (`pitwall leases renew`, actor 'cli:lease') and the
-- reconciler's activity auto-renewal (actor 'reconciler:activity') write a config_audit row
-- in the renewal transaction. The actor check from 0036 did not admit either actor, so both
-- renewals failed on Postgres. Admit them; the rest of the list is unchanged.

ALTER TABLE pitwall.config_audit
  DROP CONSTRAINT IF EXISTS config_audit_actor_check;
ALTER TABLE pitwall.config_audit
  ADD CONSTRAINT config_audit_actor_check CHECK (
    actor IN (
      'rest:admin', 'mcp:session-id', 'mcp:admin', 'rest:lease', 'rest:webhook',
      'mcp', 'system',
      'system:lease', 'system:lease-controller',
      'api:admin', 'cli',
      'cli:lease', 'reconciler:activity'
    )
  );
