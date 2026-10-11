-- Database bootstrap, run as a superuser against the target database. Idempotent;
-- roles are cluster-wide, the rest is per database.
-- docker-compose runs it on first start; the integration tests run the same file.
--
-- The passwords here are for local development only. Elsewhere, create these
-- roles out of band with real secrets.

-- Owns the schema and runs migrations. FORCE ROW LEVEL SECURITY applies to it too.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'needtoknow_owner') THEN
        CREATE ROLE needtoknow_owner LOGIN PASSWORD 'needtoknow_owner'
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

-- What the bot connects as. Data access only, granted per table by migrations.
-- Cannot bypass row-level security and owns nothing, so it can't disable it either.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'needtoknow_app') THEN
        CREATE ROLE needtoknow_app LOGIN PASSWORD 'needtoknow_app'
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I OWNER TO needtoknow_owner', current_database());
END
$$;
ALTER SCHEMA public OWNER TO needtoknow_owner;
GRANT USAGE ON SCHEMA public TO needtoknow_app;

-- Creating the extension needs a superuser, so it lives here and not in a migration.
CREATE EXTENSION IF NOT EXISTS vector;
