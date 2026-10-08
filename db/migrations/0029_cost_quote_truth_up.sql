-- COST-01: retain the structured admission quote and provider truth-up source.
--
-- Existing cost_estimate_usd values were admission amounts (normally the
-- conservative ceiling). Backfill cost_ceiling_usd from that value and leave
-- cost_quote NULL rather than inventing missing components or confidence.

ALTER TABLE pitwall.workloads
  ADD COLUMN IF NOT EXISTS cost_ceiling_usd NUMERIC(12,6),
  ADD COLUMN IF NOT EXISTS cost_quote JSONB,
  ADD COLUMN IF NOT EXISTS cost_actual_provenance TEXT,
  ADD COLUMN IF NOT EXISTS cost_reconciled_at TIMESTAMPTZ;

UPDATE pitwall.workloads
SET cost_ceiling_usd = cost_estimate_usd
WHERE cost_ceiling_usd IS NULL
  AND cost_estimate_usd IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE connamespace = 'pitwall'::regnamespace
      AND conrelid = 'pitwall.workloads'::regclass
      AND conname = 'workloads_cost_ceiling_nonneg'
  ) THEN
    ALTER TABLE pitwall.workloads
      ADD CONSTRAINT workloads_cost_ceiling_nonneg
      CHECK (cost_ceiling_usd IS NULL OR cost_ceiling_usd >= 0);
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE connamespace = 'pitwall'::regnamespace
      AND conrelid = 'pitwall.workloads'::regclass
      AND conname = 'workloads_cost_actual_provenance_requires_actual'
  ) THEN
    ALTER TABLE pitwall.workloads
      ADD CONSTRAINT workloads_cost_actual_provenance_requires_actual
      CHECK (
        cost_actual_provenance IS NULL
        OR cost_actual_usd IS NOT NULL
      );
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE connamespace = 'pitwall'::regnamespace
      AND conrelid = 'pitwall.workloads'::regclass
      AND conname = 'workloads_cost_reconciled_requires_source'
  ) THEN
    ALTER TABLE pitwall.workloads
      ADD CONSTRAINT workloads_cost_reconciled_requires_source
      CHECK (
        cost_reconciled_at IS NULL
        OR (
          cost_actual_usd IS NOT NULL
          AND cost_actual_provenance IS NOT NULL
        )
      );
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE connamespace = 'pitwall'::regnamespace
      AND conrelid = 'pitwall.workloads'::regclass
      AND conname = 'workloads_cost_ceiling_covers_estimate'
  ) THEN
    ALTER TABLE pitwall.workloads
      ADD CONSTRAINT workloads_cost_ceiling_covers_estimate
      CHECK (
        cost_ceiling_usd IS NULL
        OR cost_estimate_usd IS NULL
        OR cost_ceiling_usd >= cost_estimate_usd
      );
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE connamespace = 'pitwall'::regnamespace
      AND conrelid = 'pitwall.workloads'::regclass
      AND conname = 'workloads_cost_quote_object'
  ) THEN
    ALTER TABLE pitwall.workloads
      ADD CONSTRAINT workloads_cost_quote_object
      CHECK (cost_quote IS NULL OR jsonb_typeof(cost_quote) = 'object');
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE connamespace = 'pitwall'::regnamespace
      AND conrelid = 'pitwall.workloads'::regclass
      AND conname = 'workloads_cost_actual_provenance_nonempty'
  ) THEN
    ALTER TABLE pitwall.workloads
      ADD CONSTRAINT workloads_cost_actual_provenance_nonempty
      CHECK (
        cost_actual_provenance IS NULL
        OR btrim(cost_actual_provenance) <> ''
      );
  END IF;
END
$$;
