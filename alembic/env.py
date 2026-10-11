"""Alembic environment.

Migrations are hand-written SQL (there's no ORM metadata to autogenerate from) and
run as the schema owner role, never as the app role.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, make_url

from needtoknow.config import MigrationSettings

config = context.config

# Tests pass the URL in directly and keep their own logging setup.
url = config.attributes.get("url")
if url is None:
    url = MigrationSettings().migration_database_url.get_secret_value()  # pyright: ignore[reportCallIssue]
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)

sqlalchemy_url = make_url(url).set(drivername="postgresql+psycopg")


def run_migrations_offline() -> None:
    context.configure(url=sqlalchemy_url, literal_binds=True, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
