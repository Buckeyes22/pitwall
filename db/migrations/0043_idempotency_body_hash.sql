-- An idempotency key must replay only the same request. The conflict path compared the
-- request body against workloads.input, but the winner persists that input only after
-- admission, so a different body sent in between was accepted as a replay. The reservation
-- now records the body hash itself. The column is nullable: rows created before this
-- migration keep the workloads.input comparison as their fallback.

ALTER TABLE pitwall.idempotency_keys
  ADD COLUMN IF NOT EXISTS body_hash TEXT;

COMMENT ON COLUMN pitwall.idempotency_keys.body_hash IS
  'SHA-256 of the canonical request body that reserved the key; NULL for rows created before migration 0043';
