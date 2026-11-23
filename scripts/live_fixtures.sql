-- Live fixture objects for the live e2e harness (tests/test_live.py; TESTING.md "Live fixture objects").
--
-- Run once per account before the live suite. Safe to re-run: every statement is CREATE ... IF NOT EXISTS.
-- Snowflake CLI templating fills the two variables
-- (https://docs.snowflake.com/en/developer-guide/snowflake-cli/sql/execute-sql):
--
--   snow sql -f scripts/live_fixtures.sql -D database=MY_DB -D schema=PUBLIC
--
-- The live harness finds these objects by name in whichever database and schema they are in.

-- governance_list_grants_on_object
CREATE TABLE IF NOT EXISTS <% database %>.<% schema %>.E2E_MCP_FIXTURE_TABLE (ID NUMBER);

-- governance_list_grants_to_database_role. The role running this needs the CREATE DATABASE ROLE
-- privilege on the database (https://docs.snowflake.com/en/sql-reference/sql/create-database-role).
CREATE DATABASE ROLE IF NOT EXISTS <% database %>.E2E_MCP_FIXTURE_DATABASE_ROLE;

-- stages_get_presigned_url. GET_PRESIGNED_URL needs server-side encryption on the stage
-- (https://docs.snowflake.com/en/sql-reference/functions/get_presigned_url), and the encryption
-- type cannot be changed after the stage is created
-- (https://docs.snowflake.com/en/sql-reference/sql/create-stage).
CREATE STAGE IF NOT EXISTS <% database %>.<% schema %>.E2E_MCP_FIXTURE_STAGE ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- tasks_list_task_dependents. A new task is created suspended and this script never resumes it;
-- with no WAREHOUSE the task uses serverless compute
-- (https://docs.snowflake.com/en/sql-reference/sql/create-task).
CREATE TASK IF NOT EXISTS <% database %>.<% schema %>.E2E_MCP_FIXTURE_TASK SCHEDULE = '60 MINUTES' AS SELECT 1;
