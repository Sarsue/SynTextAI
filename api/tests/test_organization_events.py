"""The team's history: who invited, who joined, who was removed, and when.

WHY THIS EXISTS

organization_members and workspace_invites hold current state and nothing more.
A removal left no trace whatsoever, and an invite's history was its status
column being overwritten in place. So "who removed this person, and when" had no
answer anywhere in the product or the database, which is the question an owner
asks the moment somebody is missing from the list.

WHAT IS ASSERTED HERE

That the record survives the thing it describes. A removal event that vanishes
with the membership row, or that names an id whose account is later deleted and
becomes unreadable, is not a record. So the removal event is checked AFTER the
member is gone, and it must still say who they were and what they had.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _events(store, org_id):
    return await store.org_repo.list_events(org_id)


async def test_a_removal_is_still_readable_after_the_member_is_gone(store, tenant):
    """The whole point. The membership row is deleted; the record is not."""
    member = await tenant.member()
    email = await store.user_repo.get_email_from_user_id(member)

    removed = await store.org_repo.remove_member(
        tenant.org, member, actor_user_id=tenant.owner, subject_email=email
    )
    assert removed

    assert not any(
        m["user_id"] == member for m in await store.org_repo.list_members(tenant.org)
    ), "the membership row should be gone"

    events = await _events(store, tenant.org)
    removal = next((e for e in events if e["event_type"] == "member_removed"), None)
    assert removal, f"no member_removed event, got {[e['event_type'] for e in events]}"
    assert removal["subject_email"] == email, "the record must name who was removed"
    assert removal["detail"].get("role") == "staff", (
        "the role has to be captured before the row goes, or the record says "
        "somebody was removed without saying what they had"
    )


async def test_the_record_says_who_did_it(store, tenant):
    member = await tenant.member()
    email = await store.user_repo.get_email_from_user_id(member)
    owner_email = await store.user_repo.get_email_from_user_id(tenant.owner)

    await store.org_repo.remove_member(
        tenant.org, member, actor_user_id=tenant.owner, subject_email=email
    )

    removal = next(
        e for e in await _events(store, tenant.org) if e["event_type"] == "member_removed"
    )
    assert removal["actor_email"] == owner_email


async def test_events_are_scoped_to_one_organization(store, tenant):
    """A history that leaked across tenants would be worse than none."""
    member = await tenant.member()
    await store.org_repo.remove_member(
        tenant.org, member, actor_user_id=tenant.owner, subject_email="x@example.com"
    )

    stranger = await tenant.new_user("stranger")
    other_org = await store.org_repo.create_organization("Other Co", stranger)

    assert await _events(store, tenant.org), "this organization should have history"
    assert not await _events(store, other_org), "another organization must not see it"


async def test_newest_first(store, tenant):
    for i in range(3):
        await store.org_repo.record_event(
            organization_id=tenant.org,
            event_type="invite_sent",
            actor_user_id=tenant.owner,
            subject_email=f"person{i}@example.com",
        )

    events = await _events(store, tenant.org)
    assert [e["subject_email"] for e in events[:3]] == [
        "person2@example.com",
        "person1@example.com",
        "person0@example.com",
    ]


async def test_recording_never_breaks_the_thing_it_records(store, tenant):
    """Losing an audit line is bad. Failing a removal because the audit line
    failed is worse, and the removal has already happened by then."""
    await store.org_repo.record_event(
        organization_id=999_999_999,  # no such organization
        event_type="invite_sent",
        subject_email="nobody@example.com",
    )
    # Reaching here at all is the assertion: it must not raise.
