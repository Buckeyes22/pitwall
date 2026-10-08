-- 0022: served model identity for capabilities fronted by serve-model.
-- Nullable: only capabilities served through a pod lease carry a model id.
ALTER TABLE pitwall.capabilities ADD COLUMN served_model_id TEXT NULL;
