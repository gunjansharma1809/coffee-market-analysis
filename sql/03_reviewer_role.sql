-- ============================================================================
-- 03_reviewer_role.sql — create a READ-ONLY login for reviewers
-- ============================================================================
-- Run this ONCE against your Neon database. It creates a role that can connect
-- and SELECT from core + marts, but cannot INSERT, UPDATE, DELETE, or alter
-- anything. Share the reviewer connection string (not your owner .env one).
--
-- CHANGE the password below before running.
-- ============================================================================

-- 1. create the login (pick your own password)
DROP ROLE IF EXISTS reviewer;
CREATE ROLE reviewer LOGIN PASSWORD 'CHANGE_ME_to_a_real_password';

-- 2. allow connecting to the database
GRANT CONNECT ON DATABASE neondb TO reviewer;

-- 3. allow seeing the schemas
GRANT USAGE ON SCHEMA core, marts TO reviewer;

-- 4. allow reading every existing table in those schemas
GRANT SELECT ON ALL TABLES IN SCHEMA core, marts TO reviewer;

-- 5. CRITICAL: also grant SELECT on tables created by FUTURE pipeline runs.
--    Without this, re-running the pipeline creates new tables the reviewer
--    cannot see, silently breaking their access.
ALTER DEFAULT PRIVILEGES IN SCHEMA core  GRANT SELECT ON TABLES TO reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO reviewer;

