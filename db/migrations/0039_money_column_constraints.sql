-- Money columns: a lease's accrued cost can never be negative, and a volume's monthly cost keeps
-- the same six decimal places as every other USD amount (it was NUMERIC(10,2), rounding to cents).
ALTER TABLE pitwall.leases
  ADD CONSTRAINT leases_cost_accrued_nonnegative
  CHECK (cost_accrued_usd IS NULL OR cost_accrued_usd >= 0);

ALTER TABLE pitwall.volumes
  ALTER COLUMN monthly_cost_usd TYPE NUMERIC(12,6);
