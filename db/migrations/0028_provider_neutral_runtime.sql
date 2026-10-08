-- 0028: provider-neutral adapter identity, credential references, and external ids.

ALTER TABLE pitwall.providers
  ADD COLUMN adapter_id TEXT;

UPDATE pitwall.providers
SET adapter_id = CASE
  WHEN config ->> 'adapter_id' IN ('runpod', 'vast', 'together', 'lambda_cloud')
    THEN config ->> 'adapter_id'
  WHEN config ->> 'backend' IN ('runpod', 'vast', 'together', 'lambda_cloud')
    THEN config ->> 'backend'
  ELSE 'runpod'
END;

ALTER TABLE pitwall.providers
  ALTER COLUMN adapter_id SET DEFAULT 'runpod',
  ALTER COLUMN adapter_id SET NOT NULL,
  ADD CONSTRAINT providers_adapter_id_check CHECK (
    adapter_id IN ('runpod', 'vast', 'together', 'lambda_cloud')
  );

ALTER TABLE pitwall.providers
  ADD COLUMN credential_ref TEXT;

UPDATE pitwall.providers
SET credential_ref = CASE
  WHEN COALESCE(config ->> 'credential_ref', config ->> 'api_key_env')
       ~ '^[A-Za-z_][A-Za-z0-9_]*$'
    THEN COALESCE(config ->> 'credential_ref', config ->> 'api_key_env')
  WHEN adapter_id = 'vast' THEN 'VAST_API_KEY'
  WHEN adapter_id = 'together' THEN 'TOGETHER_API_KEY'
  WHEN adapter_id = 'lambda_cloud' THEN 'LAMBDA_CLOUD_API_KEY'
  ELSE 'RUNPOD_API_KEY'
END;

-- Legacy deployments could persist provider credentials inside arbitrary
-- config JSON. Derive the safe reference above first, then remove credential
-- values recursively while retaining non-secret configuration and valid
-- environment-variable references. Audit history cannot drop keys without
-- obscuring what changed, so it retains the shape with redacted values.
CREATE FUNCTION pitwall._scrub_provider_credentials(
  input_value JSONB,
  redact_values BOOLEAN
) RETURNS JSONB
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  result JSONB;
  item_key TEXT;
  item_value JSONB;
  normalized_key TEXT;
  reference_stem TEXT;
  is_credential_key BOOLEAN;
BEGIN
  CASE jsonb_typeof(input_value)
    WHEN 'object' THEN
      result := '{}'::jsonb;
      FOR item_key, item_value IN SELECT key, value FROM jsonb_each(input_value)
      LOOP
        normalized_key := lower(
          regexp_replace(
            regexp_replace(
              regexp_replace(
                btrim(item_key),
                '([A-Z]+)([A-Z][a-z])',
                '\1_\2',
                'g'
              ),
              '([a-z0-9])([A-Z])',
              '\1_\2',
              'g'
            ),
            '[^A-Za-z0-9]+',
            '_',
            'g'
          )
        );
        normalized_key := btrim(normalized_key, '_');
        reference_stem := regexp_replace(
          normalized_key,
          '_(env|ref|reference)$',
          ''
        );
        is_credential_key := normalized_key ~
          '(^|_)(access_key|access_key_id|api_key|authorization|client_secret|credential|credentials|password|private_key|secret|secret_key|token)$';

        IF reference_stem <> normalized_key
           AND reference_stem ~
             '(^|_)(access_key|access_key_id|api_key|authorization|client_secret|credential|credentials|password|private_key|secret|secret_key|token)$'
        THEN
          IF jsonb_typeof(item_value) = 'string'
             AND (item_value #>> '{}') ~ '^[A-Za-z_][A-Za-z0-9_]*$'
          THEN
            result := result || jsonb_build_object(item_key, item_value);
          ELSIF redact_values THEN
            result := result || jsonb_build_object(item_key, '[REDACTED]'::text);
          END IF;
        ELSIF is_credential_key THEN
          IF redact_values THEN
            result := result || jsonb_build_object(item_key, '[REDACTED]'::text);
          END IF;
        ELSE
          result := result || jsonb_build_object(
            item_key,
            pitwall._scrub_provider_credentials(item_value, redact_values)
          );
        END IF;
      END LOOP;
      RETURN result;
    WHEN 'array' THEN
      SELECT COALESCE(
        jsonb_agg(
          pitwall._scrub_provider_credentials(element, redact_values)
          ORDER BY ordinal
        ),
        '[]'::jsonb
      )
      INTO result
      FROM jsonb_array_elements(input_value) WITH ORDINALITY AS item(element, ordinal);
      RETURN result;
    ELSE
      RETURN input_value;
  END CASE;
END;
$$;

UPDATE pitwall.providers
SET config = pitwall._scrub_provider_credentials(config, false);

UPDATE pitwall.config_audit
SET old_value = CASE
      WHEN old_value IS NULL THEN NULL
      ELSE pitwall._scrub_provider_credentials(old_value, true)
    END,
    new_value = CASE
      WHEN new_value IS NULL THEN NULL
      ELSE pitwall._scrub_provider_credentials(new_value, true)
    END
WHERE entity_type = 'provider';

DROP FUNCTION pitwall._scrub_provider_credentials(JSONB, BOOLEAN);

ALTER TABLE pitwall.providers
  ALTER COLUMN credential_ref SET DEFAULT 'RUNPOD_API_KEY',
  ALTER COLUMN credential_ref SET NOT NULL,
  ADD CONSTRAINT providers_credential_ref_check CHECK (
    credential_ref ~ '^[A-Za-z_][A-Za-z0-9_]*$'
  );

ALTER TABLE pitwall.leases
  ADD COLUMN external_resource_id TEXT;

UPDATE pitwall.leases
SET external_resource_id = runpod_pod_id;

ALTER TABLE pitwall.leases
  ALTER COLUMN runpod_pod_id DROP NOT NULL,
  ADD CONSTRAINT leases_external_resource_id_present_check CHECK (
    external_resource_id IS NOT NULL OR runpod_pod_id IS NOT NULL
  ),
  ADD CONSTRAINT leases_runpod_resource_id_compatibility_check CHECK (
    runpod_pod_id IS NULL OR external_resource_id IS NULL
    OR runpod_pod_id = external_resource_id
  );

CREATE INDEX idx_leases_provider_external_resource
  ON pitwall.leases(provider_id, external_resource_id)
  WHERE external_resource_id IS NOT NULL;

ALTER TABLE pitwall.workloads
  ADD COLUMN external_job_id TEXT;

UPDATE pitwall.workloads
SET external_job_id = runpod_job_id
WHERE runpod_job_id IS NOT NULL;

ALTER TABLE pitwall.workloads
  ADD CONSTRAINT workloads_runpod_job_id_compatibility_check CHECK (
    runpod_job_id IS NULL OR external_job_id IS NULL
    OR runpod_job_id = external_job_id
  );

CREATE INDEX idx_workloads_provider_external_job
  ON pitwall.workloads(provider_id, external_job_id)
  WHERE external_job_id IS NOT NULL;
