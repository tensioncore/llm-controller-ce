-- LLM Controller CE v1.3 -> v1.4 upgrade
-- Run manually against the configured MySQL database while CE is stopped.
-- Reruns preserve configured speech settings and model designations.

SET @db_name = DATABASE();
SET @sql = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=@db_name AND TABLE_NAME='llm_benchmark_models' AND COLUMN_NAME='is_s2t') = 0,
  'ALTER TABLE llm_benchmark_models ADD COLUMN is_s2t TINYINT(1) NOT NULL DEFAULT 0 AFTER is_projector',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`)
VALUES
  ('speech.s2t.runtime_path', '', 'string', 'Python interpreter in the operator-installed speech environment'),
  ('speech.s2t.port', '8082', 'int', 'Private loopback speech service port'),
  ('speech.s2t.model_path', '', 'string', 'Selected managed Speech-to-Text model')
ON DUPLICATE KEY UPDATE
  `description` = VALUES(`description`);

INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`)
VALUES ('app.version', 'LLM Controller CE v1.4', 'string', 'Displayed software version')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  `value_type` = VALUES(`value_type`),
  `description` = VALUES(`description`);
