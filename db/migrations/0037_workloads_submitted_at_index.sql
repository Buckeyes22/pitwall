-- The budget gate's month-to-date query (MONTH_TO_DATE_SPEND_SQL) filters only on submitted_at,
-- with no state predicate, so the partial index limited to three states could never serve it:
-- every admission scanned the whole workloads table. Every month-to-date reader now uses that
-- one query, so a plain submitted_at index replaces the partial one.

CREATE INDEX IF NOT EXISTS idx_workloads_submitted_at ON pitwall.workloads (submitted_at);
DROP INDEX IF EXISTS pitwall.idx_workloads_month_spend;
