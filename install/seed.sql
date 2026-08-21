-- phpMyAdmin SQL Dump
-- version 5.1.1
-- https://www.phpmyadmin.net/
--
-- Host: localhost:3306
-- Generation Time: Mar 16, 2026 at 12:29 AM
-- Server version: 8.0.22
-- PHP Version: 8.3.13

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `llmcontroller`
--
CREATE DATABASE IF NOT EXISTS `llmcontroller` DEFAULT CHARACTER SET latin1 COLLATE latin1_bin;
USE `llmcontroller`;

--
-- Dumping data for table `llm_app_settings`
--

INSERT IGNORE INTO `llm_app_settings` (`key`, `value`, `value_type`, `description`, `updated_by_user_id`, `updated_at`) VALUES
('app.version', 'LLM Controller CE v1.2', 'string', 'Displayed software version', NULL, '2026-03-16 07:21:16'),
('auth.email_confirm_token_ttl_minutes', '1440', 'int', 'Email confirmation token lifetime in minutes', NULL, '2026-05-05 00:00:00'),
('auth.email_token_request_cooldown_seconds', '60', 'int', 'Cooldown for auth email token requests', NULL, '2026-05-05 00:00:00'),
('auth.public_base_url', '', 'string', 'Public base URL for auth email links', NULL, '2026-05-05 00:00:00'),
('auth.reset_token_ttl_minutes', '60', 'int', 'Password reset token lifetime in minutes', NULL, '2026-05-05 00:00:00'),
('auth.smtp.enabled', '0', 'bool', 'Enable SMTP auth emails', NULL, '2026-05-05 00:00:00'),
('auth.smtp.from_email', '', 'string', 'From address for auth emails', NULL, '2026-05-05 00:00:00'),
('auth.smtp.host', '', 'string', 'SMTP host for auth emails', NULL, '2026-05-05 00:00:00'),
('auth.smtp.password', '', 'string', 'SMTP password for auth emails', NULL, '2026-05-05 00:00:00'),
('auth.smtp.port', '587', 'int', 'SMTP port for auth emails', NULL, '2026-05-05 00:00:00'),
('auth.smtp.use_tls', '1', 'bool', 'Use STARTTLS for SMTP auth emails', NULL, '2026-05-05 00:00:00'),
('auth.smtp.username', '', 'string', 'SMTP username for auth emails', NULL, '2026-05-05 00:00:00'),
('llama.dual_gpu_split_threshold_gb', '6', 'int', 'Size threshold (GB) to enable dual-GPU tensor split', NULL, '2025-12-30 06:59:33'),
('llama.main.port', '8080', 'int', 'Main llama-server port', NULL, '2025-12-30 06:59:33'),
('llama.title.port', '8081', 'int', 'Title llama-server port', NULL, '2025-12-30 06:59:33'),
('llm.api.enabled', '0', 'bool', 'Enable the controlled OpenAI-compatible API', NULL, '2026-08-10 00:00:00'),
('llm.api.key_hash', '', 'string', 'SHA-256 hash of the generated API Bearer key', NULL, '2026-08-10 00:00:00'),
('llm.attachments.chunk_max_lines', '200', 'int', 'Maximum number of lines per attachment chunk', 1, '2026-03-16 01:35:40'),
('llm.attachments.chunk_overlap_lines', '20', 'int', 'Number of overlapping lines between attachment chunks', 1, '2026-03-16 01:35:40'),
('llm.attachments.max_context_chars', '80000', 'int', 'Maximum packaged attachment context characters sent to the model', 1, '2026-03-16 01:35:40'),
('llm.attachments.max_file_bytes', '1048576', 'int', 'Maximum size in bytes for a single attachment', 1, '2026-03-16 01:35:40'),
('llm.attachments.max_files', '8', 'int', 'Maximum number of file attachments allowed per message', 1, '2026-03-16 01:35:40'),
('llm.attachments.max_total_bytes', '4194304', 'int', 'Maximum combined size in bytes for all attachments in one message', 1, '2026-03-16 01:35:40'),
('llm.defaults.n_cpu_threads', '30', 'int', 'Default CPU threads', 1, '2026-03-16 01:35:40'),
('llm.defaults.n_gpu_layers', '360', 'int', 'Default GPU layers', 1, '2026-03-16 01:35:40'),
('llm.defaults.repeat_penalty', '1.2', 'float', 'Default repeat penalty', 1, '2026-03-16 01:35:40'),
('llm.defaults.seed', '450', 'int', 'Default random seed', 1, '2026-03-16 01:35:40'),
('llm.defaults.temperature', '0.8', 'float', 'Default temperature', 1, '2026-03-16 01:35:40'),
('llm.defaults.top_k', '30', 'int', 'Default top_k', 1, '2026-03-16 01:35:40'),
('llm.defaults.top_p', '0.85', 'float', 'Default top_p', 1, '2026-03-16 01:35:40'),
('llm.llama_server_path', 'llama-server/llama-server.exe', 'string', 'Path to llama-server executable', NULL, '2025-12-30 06:59:33'),
('llm.scan_directory', 'LLMs', 'string', 'Base folder to scan for GGUF models', 1, '2026-03-16 01:35:40'),
('llm.title_model_path', '', 'string', 'Optional enabled managed model used for title generation; blank uses the main model', NULL, '2026-08-10 00:00:00'),
('security.password_policy.level', 'strong', 'string', 'Password policy level: basic/moderate/strong/custom', NULL, '2025-12-30 06:59:33'),
('security.password_policy.min_length', '12', 'int', 'Minimum password length', NULL, '2025-12-30 06:59:33'),
('security.password_policy.require_digit', '1', 'bool', 'Require digit', NULL, '2025-12-30 06:59:33'),
('security.password_policy.require_lower', '1', 'bool', 'Require lowercase letter', NULL, '2025-12-30 06:59:33'),
('security.password_policy.require_special', '1', 'bool', 'Require special character', NULL, '2025-12-30 06:59:33'),
('security.password_policy.require_upper', '1', 'bool', 'Require uppercase letter', NULL, '2025-12-30 06:59:33');

--
-- Dumping data for table `llm_benchmark_profiles`
--

INSERT IGNORE INTO `llm_benchmark_profiles` (`id`, `name`, `settings_json`, `created_at`) VALUES
(1, 'Default v1', '{\"seed\": 42, \"top_k\": 40, \"top_p\": 0.9, \"n_threads\": 30, \"temperature\": 0.7, \"n_gpu_layers\": 360, \"repeat_penalty\": 1.1, \"warmup_enabled\": true, \"warmup_max_tokens\": 64, \"per_prompt_timeout_sec\": 900}', '2025-12-27 21:24:05');

--
-- Dumping data for table `llm_benchmark_prompt_sets`
--

INSERT IGNORE INTO `llm_benchmark_prompt_sets` (`id`, `name`, `version`, `created_at`) VALUES
(1, 'Core Suite', 'v1', '2026-05-06 22:54:05');

--
-- Dumping data for table `llm_benchmark_prompts`
--

INSERT IGNORE INTO `llm_benchmark_prompts` (`id`, `prompt_set_id`, `ordering`, `category`, `prompt_text`, `max_tokens`) VALUES
(1, 1, 1, 'practical_reasoning', 'A company claims its new battery technology can fully charge an electric car in 60 seconds with no heat generation or battery degradation. Analyze the claim critically. Explain the main engineering or physics challenges, what evidence would make the claim credible, and what red flags might suggest exaggeration or fraud.', 2500),
(2, 1, 2, 'troubleshooting', 'You are troubleshooting a Linux server that suddenly became very slow. CPU usage is low, RAM usage is moderate, but disk latency and I/O wait are extremely high. Explain the most likely causes, how you would investigate step by step, and which tools or metrics would help confirm the diagnosis.', 2500),
(3, 1, 3, 'explanation_quality', 'Explain the difference between correlation, causation, coincidence, and survivorship bias using one connected real-world example. Keep the explanation understandable to a smart non-expert while remaining technically accurate.', 2500),
(4, 1, 4, 'planning', 'A small town loses internet connectivity after a severe storm. Design a practical emergency communications plan using realistic technologies available today. Consider power loss, damaged infrastructure, limited technical expertise, cost constraints, and how information would reach the public.', 2500),
(5, 1, 5, 'uncertainty_handling', 'A user asks an AI assistant for advice about an unfamiliar topic, but the assistant is uncertain about the answer. Explain the best way the assistant should respond when confidence is low. Include the tradeoff between being helpful, being honest about uncertainty, and avoiding hallucinated information.', 2500);
COMMIT;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
