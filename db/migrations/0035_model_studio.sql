-- Alibaba Cloud Model Studio: provider type/adapter, and quota windows for Token Plan
-- Credits (subscription-credits) and pay-as-you-go month-to-date spend (pay-as-you-go).

ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_provider_type_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_provider_type_check CHECK (
  provider_type IN ('serverless_queue', 'serverless_lb', 'public_endpoint', 'pod_lease', 'openai_gateway', 'model_studio')
);
ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_adapter_id_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_adapter_id_check CHECK (
  adapter_id IN ('runpod', 'vast', 'together', 'lambda_cloud', 'openai_gateway', 'model_studio')
);
ALTER TABLE pitwall.provider_quotas DROP CONSTRAINT IF EXISTS provider_quotas_free_type_check;
ALTER TABLE pitwall.provider_quotas ADD CONSTRAINT provider_quotas_free_type_check CHECK (
  free_type IN ('recurring-daily', 'recurring-monthly', 'recurring-credit', 'recurring-uncapped',
                'one-time-initial', 'keyless', 'discontinued', 'subscription-credits', 'pay-as-you-go')
);
