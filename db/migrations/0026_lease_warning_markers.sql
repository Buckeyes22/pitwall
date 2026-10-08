-- 0026: durable expiry-warning dedupe and lease-owned capability identity.

ALTER TABLE pitwall.leases
  ADD COLUMN warning_expires_at TIMESTAMPTZ,
  ADD COLUMN warning_thresholds INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[],
  ADD COLUMN capability_name TEXT;

UPDATE pitwall.leases AS lease
SET capability_name = capability.name
FROM pitwall.providers AS provider
JOIN pitwall.capabilities AS capability ON capability.id = provider.capability_id
WHERE provider.id = lease.provider_id;

CREATE FUNCTION pitwall.populate_lease_capability_name()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  SELECT capability.name INTO NEW.capability_name
  FROM pitwall.providers AS provider
  JOIN pitwall.capabilities AS capability ON capability.id = provider.capability_id
  WHERE provider.id = NEW.provider_id;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_leases_capability_name
  BEFORE INSERT OR UPDATE OF provider_id ON pitwall.leases
  FOR EACH ROW EXECUTE FUNCTION pitwall.populate_lease_capability_name();

ALTER TABLE pitwall.leases
  ADD CONSTRAINT leases_warning_thresholds_positive CHECK (
    0 < ALL(warning_thresholds)
  );
