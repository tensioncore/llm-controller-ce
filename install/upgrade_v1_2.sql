-- LLM Controller CE v1.2 upgrade
-- Run this file manually against the configured MySQL database before restarting
-- an existing installation. It is intentionally not executed by Codex.

SET @db_name = DATABASE();

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='friendly_name') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN friendly_name VARCHAR(255) DEFAULT NULL AFTER model_name',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='notes') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN notes VARCHAR(512) DEFAULT NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='profile_general') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN profile_general TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='profile_coding') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN profile_coding TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='profile_writing') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN profile_writing TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='profile_reasoning') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN profile_reasoning TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='profile_math') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN profile_math TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='profile_agents') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN profile_agents TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='profile_images') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN profile_images TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='mmproj_path') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN mmproj_path VARCHAR(1024) DEFAULT NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='is_projector') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN is_projector TINYINT(1) NOT NULL DEFAULT 0 AFTER allow_benchmark',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`)
VALUES
  ('llm.api.enabled', '0', 'bool', 'Enable the controlled OpenAI-compatible API'),
  ('llm.api.key_hash', '', 'string', 'SHA-256 hash of the generated API Bearer key'),
  ('llm.attachments.chunk_max_lines', '200', 'int', 'Maximum number of lines per attachment chunk'),
  ('llm.attachments.chunk_overlap_lines', '20', 'int', 'Number of overlapping lines between attachment chunks'),
  ('llm.attachments.max_context_chars', '80000', 'int', 'Maximum packaged attachment context characters sent to the model'),
  ('llm.attachments.max_file_bytes', '1048576', 'int', 'Maximum size in bytes for a single attachment'),
  ('llm.attachments.max_files', '8', 'int', 'Maximum number of file attachments allowed per message'),
  ('llm.attachments.max_total_bytes', '4194304', 'int', 'Maximum combined size in bytes for all attachments in one message'),
  ('llm.title_model_path', '', 'string', 'Optional enabled managed model used for title generation; blank uses the main model')
ON DUPLICATE KEY UPDATE
  `description` = VALUES(`description`);

INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`)
VALUES ('app.version', 'LLM Controller CE v1.2', 'string', 'Displayed software version')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  `value_type` = VALUES(`value_type`),
  `description` = VALUES(`description`);
