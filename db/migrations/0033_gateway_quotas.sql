-- Free-tier gateway (ADR 0007): new provider type/adapter, quota state, and
-- the proxy model-id map. Idempotency, workloads, and cost rollups are unchanged.

ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_provider_type_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_provider_type_check CHECK (
  provider_type IN ('serverless_queue', 'serverless_lb', 'public_endpoint', 'pod_lease', 'openai_gateway')
);
ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_adapter_id_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_adapter_id_check CHECK (
  adapter_id IN ('runpod', 'vast', 'together', 'lambda_cloud', 'openai_gateway')
);

CREATE TABLE pitwall.provider_quotas (
  provider_id  TEXT NOT NULL REFERENCES pitwall.providers(id) ON DELETE CASCADE,
  pool_key     TEXT NOT NULL DEFAULT '',
  free_type    TEXT NOT NULL CHECK (free_type IN
                 ('recurring-daily','recurring-monthly','recurring-credit','recurring-uncapped',
                  'one-time-initial','keyless','discontinued')),
  window_start TIMESTAMPTZ,
  reset_at     TIMESTAMPTZ,
  budget_units TEXT,
  used_units   TEXT NOT NULL DEFAULT '0',
  tos_verdict  TEXT NOT NULL CHECK (tos_verdict IN ('ok','caution','ambiguous','avoid','unknown')),
  evidence     JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (provider_id, pool_key)
);

CREATE TABLE pitwall.provider_quota_samples (
  provider_id TEXT NOT NULL REFERENCES pitwall.providers(id) ON DELETE CASCADE,
  sampled_at  TIMESTAMPTZ NOT NULL,
  used_units  TEXT NOT NULL,
  reset_at    TIMESTAMPTZ,
  PRIMARY KEY (provider_id, sampled_at)
);
CREATE INDEX idx_provider_quota_samples_recent ON pitwall.provider_quota_samples (sampled_at DESC);

CREATE TABLE pitwall.model_id_map (
  model_id    TEXT PRIMARY KEY,
  capability  TEXT NOT NULL,
  provider    TEXT NOT NULL REFERENCES pitwall.providers(id) ON DELETE CASCADE
);
