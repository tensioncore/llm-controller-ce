-- LLM Controller CE v1.2 -> v1.3 upgrade
-- Run manually against the configured MySQL database while CE is stopped.
-- Requires the v1.2 schema. Reruns preserve existing preference rows.

CREATE TABLE IF NOT EXISTS `llm_user_preferences` (
  `user_id` int NOT NULL,
  `system_instructions` text DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  CONSTRAINT `fk_llm_user_preferences_user` FOREIGN KEY (`user_id`) REFERENCES `llm_users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`)
VALUES ('app.version', 'LLM Controller CE v1.3', 'string', 'Displayed software version')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  `value_type` = VALUES(`value_type`),
  `description` = VALUES(`description`);
