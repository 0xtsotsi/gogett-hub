"""Pod-import E2E fixtures — re-export the shared testcontainer + auth stack."""

from __future__ import annotations

import pytest_asyncio

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import scheduler_api_server

e2e_settings = e2e_fixtures.e2e_settings
scheduler_api_server = scheduler_api_server


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _scheduler_api_server(scheduler_api_server):
    """Spin up the test scheduler API so schedule imports can register jobs."""
    yield scheduler_api_server

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
scenario = e2e_fixtures.scenario
