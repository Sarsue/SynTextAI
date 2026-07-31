"""What each role may do.

Permission decisions used to be role strings compared inline at nineteen call
sites, so adding a role meant finding all of them and missing one meant a silent
grant or a silent denial. These pin the table itself, so a change to it is
visible here rather than discovered in production.
"""
import pytest

from api.core.permissions import (
    ADMIN_ROLES,
    Capability,
    can,
    capabilities_for,
    is_admin,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_a_member_can_read_and_nothing_else():
    """The product's own promise: staff ask questions, owners manage."""
    caps = capabilities_for("member")
    assert caps == frozenset({Capability.READ})

    for mutation in (
        Capability.UPLOAD_DOCUMENT,
        Capability.DELETE_DOCUMENT,
        Capability.CREATE_WORKSPACE,
        Capability.EDIT_WORKSPACE,
        Capability.DELETE_WORKSPACE,
        Capability.INVITE_MEMBER,
        Capability.REMOVE_MEMBER,
        Capability.CHANGE_MEMBER_ACCESS,
        Capability.RENAME_ORGANIZATION,
        Capability.MANAGE_BILLING,
    ):
        assert not can("member", mutation), mutation


async def test_staff_matches_member():
    """Two role vocabularies, one meaning. They must not drift apart."""
    assert capabilities_for("staff") == capabilities_for("member")


async def test_an_admin_can_do_everything_except_billing():
    caps = capabilities_for("admin")
    assert Capability.MANAGE_BILLING not in caps
    for cap in Capability:
        if cap is not Capability.MANAGE_BILLING:
            assert cap in caps, cap


async def test_an_owner_can_do_everything():
    assert capabilities_for("owner") == frozenset(Capability)


async def test_an_unknown_or_absent_role_can_do_nothing():
    """Fail closed. A typo in a role name must not grant anything."""
    for role in (None, "", "guest", "Owner ", "superuser"):
        assert capabilities_for(role) == frozenset(), role


async def test_role_names_are_case_insensitive():
    assert can("OWNER", Capability.MANAGE_BILLING)
    assert can("Admin", Capability.INVITE_MEMBER)


async def test_admin_roles_is_the_single_definition():
    assert is_admin("owner") and is_admin("admin")
    assert not is_admin("member") and not is_admin("staff") and not is_admin(None)
    assert ADMIN_ROLES == frozenset({"owner", "admin"})


async def test_every_capability_is_held_by_someone():
    """A capability no role holds is dead code that reads as a working rule."""
    held = set()
    for caps in (capabilities_for(r) for r in ("owner", "admin", "member", "staff")):
        held |= caps
    assert held == set(Capability)
