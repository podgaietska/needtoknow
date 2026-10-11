"""Real Postgres (with pgvector) in a container, bootstrapped and migrated like dev.

Needs Docker.
"""

from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

from needtoknow.db import Pool, create_pool

from .postgres import POSTGRES_IMAGE, SEED_SQL, Database, bootstrap, upgrade


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    container = PostgresContainer(
        POSTGRES_IMAGE, username="postgres", password="postgres", dbname="needtoknow"
    )
    with container:
        db = Database(container.get_container_host_ip(), int(container.get_exposed_port(5432)))
        bootstrap(db.superuser_url)
        upgrade(db.owner_url)
        with psycopg.connect(db.superuser_url, autocommit=True) as conn:
            conn.execute(SEED_SQL)
        yield db


@pytest.fixture
async def pool(database: Database) -> AsyncIterator[Pool]:
    async with create_pool(database.app_url, min_size=1, max_size=2) as pool:
        yield pool
