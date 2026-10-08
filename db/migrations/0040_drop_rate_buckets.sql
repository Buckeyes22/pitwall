-- RateBucketStore, the only reader and writer of this table, was removed; nothing else uses it.
-- Dropping the table drops its primary-key index and CHECK constraints with it.
DROP TABLE IF EXISTS pitwall.rate_buckets;
