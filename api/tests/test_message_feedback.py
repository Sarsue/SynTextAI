"""Rating an answer, and who is allowed to.

The reason this file leans on the isolation case: message_id arrives as an
integer off the URL, which is the shape of every access-control bug this
codebase has had. There is no row-level security underneath, so the check in
the route is the only thing standing between a signed-in stranger and somebody
else's conversation, one integer at a time.

The rest pins the rules the feature depends on: one rating per person per
message, only answers are rateable, and a thumbs-up does not keep the
complaint attached to a previous thumbs-down.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _conversation(store, tenant, owner_id=None, workspace_id=None):
    """A conversation with a question and an answer in it."""
    user_id = owner_id if owner_id is not None else tenant.owner
    history_id = await store.chat_repo.add_chat_history(
        "Feedback test", user_id, workspace_id=workspace_id
    )
    question_id = await store.chat_repo.add_message(
        content="does this plan cover a crown",
        sender="user",
        user_id=user_id,
        chat_history_id=history_id,
    )
    answer_id = await store.chat_repo.add_message(
        content="Yes, at 50% after the deductible. [p. 12]",
        sender="bot",
        user_id=user_id,
        chat_history_id=history_id,
    )
    return history_id, question_id, answer_id


# --- who may rate ------------------------------------------------------------

async def test_rating_someone_elses_answer_is_not_found(store, tenant, client):
    """The one that matters.

    An outsider guessing message ids must learn nothing: not the content, not
    whether the id exists. 404 for "not yours" and "not there" alike.
    """
    _, _, answer_id = await _conversation(store, tenant)
    outsider = await tenant.new_user("outsider")

    res = await client.as_(outsider).put(
        f"/api/v1/messages/{answer_id}/feedback", json={"rating": -1}
    )

    assert res.status_code == 404

    # And nothing was written on their behalf.
    stored = await store.chat_repo.get_messages_for_chat_history(
        (await store.chat_repo.get_all_user_chat_histories(tenant.owner))[0]["id"],
        tenant.owner,
    )
    answer = next(m for m in stored if m["id"] == answer_id)
    assert answer["feedback"] is None


async def test_rating_a_message_that_does_not_exist_is_not_found(tenant, client):
    res = await client.as_(tenant.owner).put(
        "/api/v1/messages/99999999/feedback", json={"rating": 1}
    )
    assert res.status_code == 404


async def test_rating_your_own_question_is_refused(store, tenant, client):
    """Only answers are rateable. Rating your own question is confused input,
    not a permission problem, so it says 400 rather than 404."""
    _, question_id, _ = await _conversation(store, tenant)

    res = await client.as_(tenant.owner).put(
        f"/api/v1/messages/{question_id}/feedback", json={"rating": -1}
    )

    assert res.status_code == 400


# --- what gets stored --------------------------------------------------------

async def test_a_thumbs_down_records_the_chip_and_the_comment(store, tenant, client):
    _, _, answer_id = await _conversation(store, tenant)

    res = await client.as_(tenant.owner).put(
        f"/api/v1/messages/{answer_id}/feedback",
        json={
            "rating": -1,
            "reason": "wrong_source",
            "comment": "cited the 2019 policy, we are on the 2024 one",
        },
    )

    assert res.status_code == 200
    assert res.json() == {
        "rating": -1,
        "reason": "wrong_source",
        "comment": "cited the 2019 policy, we are on the 2024 one",
    }


async def test_rating_again_replaces_rather_than_accumulating(store, tenant, client):
    """The unique constraint, from the outside.

    Somebody who presses thumbs-down, is then satisfied by a second read and
    presses thumbs-up, must end with one rating. Two rows that disagree would
    make every count in the report wrong.
    """
    _, _, answer_id = await _conversation(store, tenant)
    caller = client.as_(tenant.owner)

    await caller.put(
        f"/api/v1/messages/{answer_id}/feedback",
        json={"rating": -1, "reason": "incomplete", "comment": "missed the waiting period"},
    )
    second = await caller.put(f"/api/v1/messages/{answer_id}/feedback", json={"rating": 1})

    assert second.status_code == 200
    # The complaint is gone, not merely outvoted. Left attached, it would read
    # as a criticism of an answer they just said was good.
    assert second.json() == {"rating": 1, "reason": None, "comment": None}

    rows = await store.chat_repo.get_messages_for_chat_history(
        (await store.chat_repo.get_all_user_chat_histories(tenant.owner))[0]["id"],
        tenant.owner,
    )
    answer = next(m for m in rows if m["id"] == answer_id)
    assert answer["feedback"] == {"rating": 1, "reason": None, "comment": None}


async def test_feedback_comes_back_with_the_conversation(store, tenant, client):
    """Otherwise the thumbs reset on reload and invite a second rating of the
    same answer."""
    history_id, _, answer_id = await _conversation(store, tenant)
    await client.as_(tenant.owner).put(
        f"/api/v1/messages/{answer_id}/feedback", json={"rating": -1, "reason": "wrong"}
    )

    res = await client.as_(tenant.owner).get(
        f"/api/v1/histories/messages?history_id={history_id}"
    )

    assert res.status_code == 200
    answer = next(m for m in res.json() if m["id"] == answer_id)
    assert answer["feedback"]["rating"] == -1
    assert answer["feedback"]["reason"] == "wrong"


async def test_clearing_a_rating_removes_it(store, tenant, client):
    """Pressing the same thumb again means "never mind"."""
    history_id, _, answer_id = await _conversation(store, tenant)
    caller = client.as_(tenant.owner)
    await caller.put(f"/api/v1/messages/{answer_id}/feedback", json={"rating": 1})

    res = await caller.delete(f"/api/v1/messages/{answer_id}/feedback")

    assert res.status_code == 200
    rows = (await caller.get(f"/api/v1/histories/messages?history_id={history_id}")).json()
    assert next(m for m in rows if m["id"] == answer_id)["feedback"] is None


async def test_clearing_someone_elses_rating_is_not_found(store, tenant, client):
    _, _, answer_id = await _conversation(store, tenant)
    await client.as_(tenant.owner).put(
        f"/api/v1/messages/{answer_id}/feedback", json={"rating": 1}
    )
    outsider = await tenant.new_user("outsider-delete")

    res = await client.as_(outsider).delete(f"/api/v1/messages/{answer_id}/feedback")

    assert res.status_code == 404


# --- what is refused ---------------------------------------------------------

@pytest.mark.parametrize("rating", [0, 5, -2, 100])
async def test_only_minus_one_and_one_are_ratings(store, tenant, client, rating):
    """A check constraint backs this in the database, but a 422 is a better
    answer than an integrity error surfacing as a 500."""
    _, _, answer_id = await _conversation(store, tenant)

    res = await client.as_(tenant.owner).put(
        f"/api/v1/messages/{answer_id}/feedback", json={"rating": rating}
    )

    assert res.status_code == 422


async def test_an_unknown_chip_is_refused(store, tenant, client):
    """Free text belongs in comment. A free-text reason would make the tally
    in the report meaningless."""
    _, _, answer_id = await _conversation(store, tenant)

    res = await client.as_(tenant.owner).put(
        f"/api/v1/messages/{answer_id}/feedback",
        json={"rating": -1, "reason": "it was bad"},
    )

    assert res.status_code == 422


async def test_an_overlong_comment_is_refused(store, tenant, client):
    _, _, answer_id = await _conversation(store, tenant)

    res = await client.as_(tenant.owner).put(
        f"/api/v1/messages/{answer_id}/feedback",
        json={"rating": -1, "comment": "x" * 501},
    )

    assert res.status_code == 422


# --- the join that makes it diagnostic ---------------------------------------

async def test_feedback_records_the_run_that_produced_the_answer(store, tenant, client):
    """A rating alone is a number. The run is what makes it a diagnosis.

    Checks the whole link: the worker records which message a run produced, and
    rating that message picks the run up without the client naming it.
    """
    _, _, answer_id = await _conversation(store, tenant)
    run_id = await store.agent_run_repo.enqueue_run(
        run_type="answer_query",
        agent_name="QueryAgent",
        agent_version=None,
        payload={"message": "does this plan cover a crown"},
        user_id=tenant.owner,
    )
    assert await store.chat_repo.link_run_to_message(run_id, answer_id)

    res = await client.as_(tenant.owner).put(
        f"/api/v1/messages/{answer_id}/feedback", json={"rating": -1, "reason": "wrong"}
    )
    assert res.status_code == 200

    stored = await store.chat_repo.feedback_for_report(limit=50)
    mine = next(f for f in stored if f["message_id"] == answer_id)
    assert str(mine["agent_run_id"]) == str(run_id)
    assert mine["question"] == "does this plan cover a crown"
