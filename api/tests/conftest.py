"""Shared fixtures for the integration tests.

These run against a real Postgres, not a mock. The rules under test are
expressed as SQL joins, so a mocked session would only prove the mock behaves
as written. Point them at the local stack:

    docker compose --env-file .env.dev -f docker-compose.local.yml exec \
        syntextaiapp python -m pytest api/tests -v

Every test creates its own organization and users and removes them afterwards,
so a run leaves no trace in whatever database it was pointed at.
"""
import uuid

import pytest
import pytest_asyncio

from api.models.async_db import get_database_url
from api.repositories.repository_manager import RepositoryManager


@pytest.fixture
def store() -> RepositoryManager:
    """A repository manager per test.

    Deliberately not session-scoped. The async engine binds its connection pool
    to the event loop that created it, and pytest-asyncio gives each test a
    fresh loop, so a shared manager fails on every test after the first with
    connections belonging to a closed loop.
    """
    return RepositoryManager(database_url=get_database_url())


@pytest_asyncio.fixture(loop_scope="session")
async def tenant(store):
    """An organization with an owner, torn down afterwards.

    Emails are uniquely suffixed so a failed run cannot collide with the next
    one on the users table's unique constraint.
    """
    tag = uuid.uuid4().hex[:8]
    created_users = []

    async def make_user(name: str) -> int:
        email = f"{name}-{tag}@test.invalid"
        await store.user_repo.add_user(email, f"{name}-{tag}")
        uid = await store.user_repo.get_user_id_from_email(email)
        created_users.append(uid)
        return uid

    owner_id = await make_user("owner")
    org_id = await store.org_repo.create_organization(f"Test Co {tag}", owner_id)

    class Tenant:
        org = org_id
        owner = owner_id
        new_user = staticmethod(make_user)

        @staticmethod
        async def workspace(name: str) -> int:
            return await store.workspace_repo.create_workspace(user_id=owner_id, name=name)

        @staticmethod
        async def member(name: str = "member", scope: str = "workspace", workspaces=None) -> int:
            uid = await make_user(name)
            await store.org_repo.add_member(org_id, uid, role="member")
            await store.org_repo.set_member_access(org_id, uid, scope, workspaces or [])
            return uid

    yield Tenant

    await store.org_repo.delete_organization(org_id)
    for uid in created_users:
        await store.user_repo.delete_user_account(uid)
