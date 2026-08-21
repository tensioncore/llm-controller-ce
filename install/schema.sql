-- phpMyAdmin SQL Dump
-- version 5.1.1
-- https://www.phpmyadmin.net/
--
-- Host: localhost:3306
-- Generation Time: Mar 16, 2026 at 12:28 AM
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

-- --------------------------------------------------------

--
-- Table structure for table `llm_app_settings`
--

CREATE TABLE IF NOT EXISTS `llm_app_settings` (
  `key` varchar(128) NOT NULL,
  `value` text NOT NULL,
  `value_type` enum('string','int','float','bool','json') NOT NULL DEFAULT 'string',
  `description` varchar(255) DEFAULT NULL,
  `updated_by_user_id` int DEFAULT NULL,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `llm_benchmark_models`
--

CREATE TABLE IF NOT EXISTS `llm_benchmark_models` (
  `id` bigint UNSIGNED NOT NULL,
  `fingerprint` varchar(128) NOT NULL,
  `model_name` varchar(255) NOT NULL,
  `friendly_name` varchar(255) DEFAULT NULL,
  `model_path` text NOT NULL,
  `file_size` bigint UNSIGNED NOT NULL,
  `mtime` bigint UNSIGNED NOT NULL,
  `first_seen_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_seen_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `is_enabled` tinyint(1) NOT NULL DEFAULT '1',
  `is_favorite` tinyint(1) NOT NULL DEFAULT '0',
  `allow_benchmark` tinyint(1) NOT NULL DEFAULT '1',
  `is_projector` TINYINT(1) NOT NULL DEFAULT 0,
  `is_present` tinyint(1) NOT NULL DEFAULT '1',
  `notes` varchar(512) DEFAULT NULL,
  `profile_general` tinyint(1) NOT NULL DEFAULT '0',
  `profile_coding` tinyint(1) NOT NULL DEFAULT '0',
  `profile_writing` tinyint(1) NOT NULL DEFAULT '0',
  `profile_reasoning` tinyint(1) NOT NULL DEFAULT '0',
  `profile_math` tinyint(1) NOT NULL DEFAULT '0',
  `profile_agents` tinyint(1) NOT NULL DEFAULT '0',
  `profile_images` tinyint(1) NOT NULL DEFAULT '0',
  `mmproj_path` varchar(1024) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `llm_benchmark_profiles`
--

CREATE TABLE IF NOT EXISTS `llm_benchmark_profiles` (
  `id` bigint UNSIGNED NOT NULL,
  `name` varchar(120) NOT NULL,
  `settings_json` json NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `llm_benchmark_prompts`
--

CREATE TABLE IF NOT EXISTS `llm_benchmark_prompts` (
  `id` bigint UNSIGNED NOT NULL,
  `prompt_set_id` bigint UNSIGNED NOT NULL,
  `ordering` int NOT NULL,
  `category` enum('knowledge','coding','practical_reasoning','troubleshooting','explanation_quality','planning','uncertainty_handling') NOT NULL,
  `prompt_text` text NOT NULL,
  `max_tokens` int NOT NULL DEFAULT '256'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `llm_benchmark_prompt_sets`
--

CREATE TABLE IF NOT EXISTS `llm_benchmark_prompt_sets` (
  `id` bigint UNSIGNED NOT NULL,
  `name` varchar(120) NOT NULL,
  `version` varchar(30) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `llm_benchmark_results`
--

CREATE TABLE IF NOT EXISTS `llm_benchmark_results` (
  `id` bigint UNSIGNED NOT NULL,
  `run_id` bigint UNSIGNED NOT NULL,
  `prompt_id` bigint UNSIGNED NOT NULL,
  `success` tinyint(1) NOT NULL DEFAULT '1',
  `error` varchar(255) DEFAULT NULL,
  `response_text` mediumtext,
  `tokens_prompt` int NOT NULL DEFAULT '0',
  `tokens_generated` int NOT NULL DEFAULT '0',
  `ttft_ms` int NOT NULL DEFAULT '0',
  `prompt_eval_tps` double NOT NULL DEFAULT '0',
  `eval_tps` double NOT NULL DEFAULT '0',
  `total_ms` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `llm_benchmark_runs`
--

CREATE TABLE IF NOT EXISTS `llm_benchmark_runs` (
  `id` bigint UNSIGNED NOT NULL,
  `model_id` bigint UNSIGNED NOT NULL,
  `profile_id` bigint UNSIGNED NOT NULL,
  `prompt_set_id` bigint UNSIGNED NOT NULL,
  `status` enum('RUNNING','PASS','FAILED') NOT NULL DEFAULT 'RUNNING',
  `fail_reason` varchar(255) DEFAULT NULL,
  `fail_prompt_id` bigint UNSIGNED DEFAULT NULL,
  `started_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `ended_at` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `llm_users`
--

CREATE TABLE IF NOT EXISTS `llm_users` (
  `id` int NOT NULL,
  `email` varchar(255) COLLATE latin1_bin NOT NULL,
  `password_hash` varchar(255) COLLATE latin1_bin NOT NULL,
  `role` enum('admin','user','moderator') COLLATE latin1_bin NOT NULL DEFAULT 'user',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `last_login` datetime DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `invite_token` varchar(64) COLLATE latin1_bin DEFAULT NULL,
  `quota` int DEFAULT '100000',
  `must_change_pw` tinyint(1) DEFAULT '0',
  `temp_password` varchar(255) COLLATE latin1_bin DEFAULT NULL,
  `email_confirmed_at` datetime DEFAULT NULL,
  `pending_email` varchar(255) COLLATE latin1_bin DEFAULT NULL,
  `email_confirm_token_hash` char(64) COLLATE latin1_bin DEFAULT NULL,
  `email_confirm_token_expires_at` datetime DEFAULT NULL,
  `reset_token_hash` char(64) COLLATE latin1_bin DEFAULT NULL,
  `reset_token_expires_at` datetime DEFAULT NULL,
  `password_changed_at` datetime DEFAULT NULL,
  `session_version` int NOT NULL DEFAULT '1'
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_bin;

-- --------------------------------------------------------

--
-- Table structure for table `login_attempts`
--

CREATE TABLE IF NOT EXISTS `login_attempts` (
  `id` int NOT NULL,
  `username` varchar(255) COLLATE latin1_bin DEFAULT NULL,
  `ip_address` varchar(45) COLLATE latin1_bin DEFAULT NULL,
  `success` tinyint(1) DEFAULT NULL,
  `attempt_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_bin;

--
-- Indexes for dumped tables
--

--
-- Indexes for table `llm_app_settings`
--
ALTER TABLE `llm_app_settings`
  ADD PRIMARY KEY (`key`),
  ADD KEY `idx_updated_at` (`updated_at`),
  ADD KEY `idx_updated_by_user_id` (`updated_by_user_id`);

--
-- Indexes for table `llm_benchmark_models`
--
ALTER TABLE `llm_benchmark_models`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `uq_fingerprint` (`fingerprint`),
  ADD KEY `idx_enabled_favorite` (`is_enabled`,`is_favorite`),
  ADD KEY `idx_allow_benchmark` (`allow_benchmark`),
  ADD KEY `idx_is_present` (`is_present`);

--
-- Indexes for table `llm_benchmark_profiles`
--
ALTER TABLE `llm_benchmark_profiles`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `uq_profile_name` (`name`);

--
-- Indexes for table `llm_benchmark_prompts`
--
ALTER TABLE `llm_benchmark_prompts`
  ADD PRIMARY KEY (`id`),
  ADD KEY `idx_promptset` (`prompt_set_id`);

--
-- Indexes for table `llm_benchmark_prompt_sets`
--
ALTER TABLE `llm_benchmark_prompt_sets`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `uq_promptset` (`name`,`version`);

--
-- Indexes for table `llm_benchmark_results`
--
ALTER TABLE `llm_benchmark_results`
  ADD PRIMARY KEY (`id`),
  ADD KEY `idx_results_run` (`run_id`),
  ADD KEY `idx_results_prompt` (`prompt_id`);

--
-- Indexes for table `llm_benchmark_runs`
--
ALTER TABLE `llm_benchmark_runs`
  ADD PRIMARY KEY (`id`),
  ADD KEY `idx_run_model` (`model_id`),
  ADD KEY `idx_run_profile` (`profile_id`),
  ADD KEY `idx_run_promptset` (`prompt_set_id`),
  ADD KEY `idx_run_status` (`status`);

--
-- Indexes for table `llm_users`
--
ALTER TABLE `llm_users`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `email` (`email`),
  ADD KEY `idx_llm_users_reset_token_hash` (`reset_token_hash`),
  ADD KEY `idx_llm_users_email_confirm_token_hash` (`email_confirm_token_hash`),
  ADD KEY `idx_llm_users_pending_email` (`pending_email`);

--
-- Indexes for table `login_attempts`
--
ALTER TABLE `login_attempts`
  ADD PRIMARY KEY (`id`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `llm_benchmark_models`
--
ALTER TABLE `llm_benchmark_models`
  MODIFY `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `llm_benchmark_profiles`
--
ALTER TABLE `llm_benchmark_profiles`
  MODIFY `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `llm_benchmark_prompts`
--
ALTER TABLE `llm_benchmark_prompts`
  MODIFY `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `llm_benchmark_prompt_sets`
--
ALTER TABLE `llm_benchmark_prompt_sets`
  MODIFY `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `llm_benchmark_results`
--
ALTER TABLE `llm_benchmark_results`
  MODIFY `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `llm_benchmark_runs`
--
ALTER TABLE `llm_benchmark_runs`
  MODIFY `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `llm_users`
--
ALTER TABLE `llm_users`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `login_attempts`
--
ALTER TABLE `login_attempts`
  MODIFY `id` int NOT NULL AUTO_INCREMENT;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `llm_benchmark_prompts`
--
ALTER TABLE `llm_benchmark_prompts`
  ADD CONSTRAINT `fk_prompts_promptset` FOREIGN KEY (`prompt_set_id`) REFERENCES `llm_benchmark_prompt_sets` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `llm_benchmark_results`
--
ALTER TABLE `llm_benchmark_results`
  ADD CONSTRAINT `fk_results_prompt` FOREIGN KEY (`prompt_id`) REFERENCES `llm_benchmark_prompts` (`id`) ON DELETE RESTRICT,
  ADD CONSTRAINT `fk_results_run` FOREIGN KEY (`run_id`) REFERENCES `llm_benchmark_runs` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `llm_benchmark_runs`
--
ALTER TABLE `llm_benchmark_runs`
  ADD CONSTRAINT `fk_runs_model` FOREIGN KEY (`model_id`) REFERENCES `llm_benchmark_models` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `fk_runs_profile` FOREIGN KEY (`profile_id`) REFERENCES `llm_benchmark_profiles` (`id`) ON DELETE RESTRICT,
  ADD CONSTRAINT `fk_runs_promptset` FOREIGN KEY (`prompt_set_id`) REFERENCES `llm_benchmark_prompt_sets` (`id`) ON DELETE RESTRICT;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
