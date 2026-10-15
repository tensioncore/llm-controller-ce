-- LLM Controller CE v1.4 -> v1.5: model settings, Memory, and version.
-- Run manually against the configured MySQL database while CE is stopped.
-- Reruns preserve the operator's and users' choices.
INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`)
VALUES ('llm.memory.enabled', '0', 'bool', 'Enable local personal and Project conversational Memory')
ON DUPLICATE KEY UPDATE `description` = VALUES(`description`);

SET @db_name = DATABASE();
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_user_preferences' AND COLUMN_NAME='personal_memory_enabled') = 0,
  'ALTER TABLE llm_user_preferences ADD COLUMN personal_memory_enabled TINYINT(1) NOT NULL DEFAULT 1 AFTER system_instructions',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='starts_in_thinking_mode') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN starts_in_thinking_mode TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Project Memory mode lives in CE's local chat SQLite database, not MySQL.
-- helpers.init_db() adds projects.memory_mode if missing on normal CE startup.

INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`)
VALUES ('app.version', 'LLM Controller CE v1.5', 'string', 'Displayed software version')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  `value_type` = VALUES(`value_type`),
  `description` = VALUES(`description`);
