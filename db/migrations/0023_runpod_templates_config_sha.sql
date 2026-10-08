-- 0023: key generated RunPod templates by their complete non-secret config.
-- Existing image-only rows receive a deterministic legacy identity. New rows
-- use the application SHA-256 over the full image reference, command, ports, and sorted
-- non-secret environment key names. Environment values are never persisted.

ALTER TABLE pitwall.runpod_templates
  ADD COLUMN config_sha TEXT;

UPDATE pitwall.runpod_templates
SET config_sha = md5('legacy:' || image_sha) || md5('legacy-config:' || image_sha)
WHERE config_sha IS NULL;

ALTER TABLE pitwall.runpod_templates
  ALTER COLUMN config_sha SET NOT NULL,
  DROP CONSTRAINT runpod_templates_name_image_sha_key,
  ADD CONSTRAINT runpod_templates_name_config_sha_key UNIQUE (name, config_sha);

CREATE INDEX idx_runpod_templates_config_sha
  ON pitwall.runpod_templates(config_sha);
