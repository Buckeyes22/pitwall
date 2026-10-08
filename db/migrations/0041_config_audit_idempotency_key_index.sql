-- The RunPod mutation journals (control-plane and volume-file) look up their newest
-- config_audit row by idempotency key while holding the key's advisory lock. Without an
-- index that lookup scans every lease, provider, template, and volume audit row; this
-- partial expression index covers only journal rows.

CREATE INDEX IF NOT EXISTS idx_config_audit_mutation_idempotency_key
  ON pitwall.config_audit ((new_value ->> 'idempotency_key'))
  WHERE new_value ->> 'kind' IN ('runpod_control_plane_mutation', 'runpod_volume_file_mutation');
