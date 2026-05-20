-- Migration 001: rename audit_log → cyllene_audit_log + add bulletproof columns
-- Run once against the MySQL database.
-- Safe to run multiple times (IF EXISTS / IF NOT EXISTS guards).

-- Step 1: Rename table (only if old name still exists)
SET @old_exists = (
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = DATABASE() AND table_name = 'audit_log'
);
SET @new_exists = (
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = DATABASE() AND table_name = 'cyllene_audit_log'
);

-- Rename only if old exists and new doesn't
SET @sql = IF(
    @old_exists > 0 AND @new_exists = 0,
    'RENAME TABLE audit_log TO cyllene_audit_log',
    'SELECT "audit_log rename skipped" AS info'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 2: Add new columns (IF NOT EXISTS requires MySQL 8.0+)
-- actor_email
ALTER TABLE cyllene_audit_log
    ADD COLUMN IF NOT EXISTS actor_email VARCHAR(255) NULL AFTER user_id;

-- status
ALTER TABLE cyllene_audit_log
    ADD COLUMN IF NOT EXISTS status VARCHAR(16) NULL DEFAULT 'success' AFTER action;

-- diff (before/after JSON)
ALTER TABLE cyllene_audit_log
    ADD COLUMN IF NOT EXISTS diff JSON NULL AFTER details;

-- Step 3: Add index on actor_email for filtering
ALTER TABLE cyllene_audit_log
    ADD INDEX IF NOT EXISTS idx_actor_email (actor_email);

SELECT 'Migration 001 complete' AS info;
