"""Documents SyntextAI writes, and the wall between them and the corpus.

The wall is the feature. If a generated draft could be retrieved before a person
approved it, the model's own output would become the model's own source of
truth: it writes a plausible SOP with one wrong figure, that gets ingested, and
afterwards it cites itself with a page reference indistinguishable from a real
one. The customer has no way to tell.

So the first tests here are not about generating anything. They are about a
draft being unable to answer a question, and about approval being the only door.
"""
import uuid

import pytest
import pytest_asyncio

from api.routes import drafts as drafts_route

pytestmark = pytest.mark.asyncio(loop_scope="session")

DIM = 1024
QUERY_VEC = [0.05] * DIM

SOURCE_TEXT = (
    "Sterilisation procedure. Instruments are cleaned in the ultrasonic bath "
    "for 10 minutes, then autoclaved at 134 degrees for 3 minutes."
)
# A figure that appears in no document, so retrieving it proves the draft was
# the source rather than the workspace.
DRAFT_TEXT = "# Sterilisation SOP\n\nAutoclave at 999 degrees for 42 minutes."


@pytest_asyncio.fixture(loop_scope="session")
async def paying(store, tenant):
    """Writing a document into a workspace is adding to it, so it is gated on
    the subscription exactly like an upload. Every test that generates or
    approves needs this."""
    from api.core.plans import STARTER

    await store.user_repo.add_or_update_subscription(
        user_id=tenant.owner,
        organization_id=tenant.org,
        stripe_customer_id="cus_test_drafts",
        stripe_subscription_id="sub_test_drafts",
        status="active",
        seats=STARTER.included_seats,
        plan_key="starter",
    )
    return tenant


@pytest_asyncio.fixture(loop_scope="session")
async def workspace(store, tenant):
    from api.models.orm_models import Chunk, Segment

    ws = await tenant.workspace("Drafting")
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name=f"sop-{uuid.uuid4().hex[:6]}.pdf",
        file_url="", workspace_id=ws,
    )
    async with store.file_repo.get_async_session() as session:
        seg = Segment(file_id=file_id, page_number=1, content=SOURCE_TEXT)
        session.add(seg)
        await session.commit()
        session.add(Chunk(
            file_id=file_id, segment_id=seg.id, content=SOURCE_TEXT,
            embedding=QUERY_VEC, content_hash="h-" + uuid.uuid4().hex[:8],
        ))
        await session.commit()
    return {"id": ws, "file_id": file_id}


@pytest_asyncio.fixture(loop_scope="session")
async def draft(store, tenant, workspace):
    return await store.draft_repo.create(
        workspace_id=workspace["id"],
        created_by=tenant.owner,
        title="Sterilisation SOP",
        prompt="write a sterilisation SOP",
        content=DRAFT_TEXT,
        sources=[{"segment": 1, "file_id": workspace["file_id"],
                  "file_name": "sop.pdf", "page_number": 1}],
    )


# --- the wall ----------------------------------------------------------------

async def test_a_draft_cannot_answer_a_question(store, tenant, workspace, draft):
    """The whole point. A draft exists and retrieval cannot see it.

    Searched for a figure that appears only in the draft. If this ever returns
    a hit, a generated document has become its own source and the feature is
    actively harmful rather than merely incomplete.
    """
    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="autoclave at 999 degrees for 42 minutes",
        query_embedding=QUERY_VEC,
        workspace_id=workspace["id"],
    )
    for h in hits:
        assert "999" not in (h.get("content") or ""), (
            "a generated draft was retrieved before anybody approved it"
        )


async def test_the_draft_is_not_a_file(store, tenant, workspace, draft):
    """It must not appear as a document either, or somebody will cite it by hand."""
    listed = await store.file_repo.get_files_for_user(
        tenant.owner, workspace_id=workspace["id"], limit=100
    )
    names = {f["file_name"] for f in listed["items"]}
    assert "Sterilisation SOP.md" not in names


# --- editing, which is why this is a document and not a chat message ---------

async def test_the_owner_can_edit_a_draft(store, tenant, draft, client):
    res = await client.as_(tenant.owner).patch(
        f"/api/v1/drafts/{draft['id']}",
        json={"content": "# Sterilisation SOP\n\nEdited by a person."},
    )
    assert res.status_code == 200, res.text
    assert "Edited by a person" in res.json()["content"]


async def test_an_empty_edit_is_refused(store, tenant, draft, client):
    res = await client.as_(tenant.owner).patch(f"/api/v1/drafts/{draft['id']}", json={})
    assert res.status_code == 400


async def test_the_list_does_not_carry_every_document_body(store, tenant, workspace, draft, client):
    """Twenty whole documents to render a list of titles is the mistake
    message_feedback was split out of `messages` to avoid."""
    res = await client.as_(tenant.owner).get(f"/api/v1/drafts?workspace_id={workspace['id']}")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert items and "content" not in items[0]
    assert items[0]["title"] == "Sterilisation SOP"


# --- approval is the only door ------------------------------------------------

async def test_approving_creates_a_real_document_and_queues_ingestion(
    store, tenant, paying, workspace, draft, client, monkeypatch
):
    """Approval goes through the upload path rather than around it."""
    async def fake_upload(data, workspace_id, file_id, filename):
        fake_upload.seen = {"data": data, "filename": filename}
        return f"https://storage.example/{workspace_id}/{file_id}-{filename}"
    monkeypatch.setattr(drafts_route, "upload_bytes_to_gcs", fake_upload)

    res = await client.as_(tenant.owner).post(f"/api/v1/drafts/{draft['id']}/ingest")
    assert res.status_code == 202, res.text
    file_id = res.json()["file_id"]

    created = await store.file_repo.get_file_by_id(file_id)
    assert created is not None
    assert created["workspace_id"] == workspace["id"]

    # The stored bytes say a machine wrote it and a person approved it. Once
    # this is retrievable it will be read by people who were not in the room.
    body = fake_upload.seen["data"].decode()
    assert "Written by SyntextAI" in body
    assert "approved by a person" in body

    refreshed = await store.draft_repo.get(draft["id"])
    assert refreshed["status"] == "ingested"
    assert refreshed["ingested_file_id"] == file_id


async def test_approving_twice_is_refused(
    store, tenant, paying, draft, client, monkeypatch
):
    async def fake_upload(data, workspace_id, file_id, filename):
        return f"https://storage.example/{file_id}-{filename}"
    monkeypatch.setattr(drafts_route, "upload_bytes_to_gcs", fake_upload)

    first = await client.as_(tenant.owner).post(f"/api/v1/drafts/{draft['id']}/ingest")
    assert first.status_code == 202
    second = await client.as_(tenant.owner).post(f"/api/v1/drafts/{draft['id']}/ingest")
    assert second.status_code == 409


async def test_a_failed_upload_leaves_no_unopenable_document(
    store, tenant, paying, workspace, draft, client, monkeypatch
):
    """A files row with no stored object is a document that appears in the list
    and can never be opened. Same rule as an upload."""
    async def failing_upload(data, workspace_id, file_id, filename):
        return None
    monkeypatch.setattr(drafts_route, "upload_bytes_to_gcs", failing_upload)

    before = await store.file_repo.get_files_for_user(
        tenant.owner, workspace_id=workspace["id"], limit=100
    )
    res = await client.as_(tenant.owner).post(f"/api/v1/drafts/{draft['id']}/ingest")
    assert res.status_code == 500
    after = await store.file_repo.get_files_for_user(
        tenant.owner, workspace_id=workspace["id"], limit=100
    )
    assert after["total"] == before["total"]

    still_draft = await store.draft_repo.get(draft["id"])
    assert still_draft["status"] == "draft"


async def test_deleting_a_draft_keeps_the_document_approved_from_it(
    store, tenant, paying, draft, client, monkeypatch
):
    async def fake_upload(data, workspace_id, file_id, filename):
        return f"https://storage.example/{file_id}-{filename}"
    monkeypatch.setattr(drafts_route, "upload_bytes_to_gcs", fake_upload)

    ingested = await client.as_(tenant.owner).post(f"/api/v1/drafts/{draft['id']}/ingest")
    file_id = ingested.json()["file_id"]

    deleted = await client.as_(tenant.owner).delete(f"/api/v1/drafts/{draft['id']}")
    assert deleted.status_code == 200

    assert await store.draft_repo.get(draft["id"]) is None
    assert await store.file_repo.get_file_by_id(file_id) is not None


# --- who may do it ------------------------------------------------------------

async def test_staff_cannot_edit_a_draft(store, tenant, workspace, draft, client):
    """Owners manage documents; staff ask questions."""
    member = await tenant.member(scope="workspace", workspaces=[workspace["id"]])
    res = await client.as_(member).patch(
        f"/api/v1/drafts/{draft['id']}", json={"content": "changed"},
    )
    assert res.status_code == 403


async def test_staff_cannot_approve_a_draft_into_the_knowledge_base(
    store, tenant, workspace, draft, client
):
    """The approval gate is the whole safety story, so it is held to the
    capability that adds documents, not to being able to read them."""
    member = await tenant.member(scope="workspace", workspaces=[workspace["id"]])
    res = await client.as_(member).post(f"/api/v1/drafts/{draft['id']}/ingest")
    assert res.status_code == 403


async def test_an_outsider_cannot_read_a_draft(store, tenant, draft, client):
    outsider = await tenant.new_user("outsider")
    res = await client.as_(outsider).get(f"/api/v1/drafts/{draft['id']}")
    assert res.status_code == 403


async def test_a_draft_that_does_not_exist_is_404(store, tenant, client):
    res = await client.as_(tenant.owner).get("/api/v1/drafts/99999999")
    assert res.status_code == 404


async def test_listing_drafts_in_an_unreachable_workspace_is_refused(
    store, tenant, workspace, client
):
    other = await tenant.workspace("Elsewhere")
    member = await tenant.member(scope="workspace", workspaces=[workspace["id"]])
    res = await client.as_(member).get(f"/api/v1/drafts?workspace_id={other}")
    assert res.status_code == 403


# --- generating ---------------------------------------------------------------

async def test_generating_refuses_when_no_document_covers_the_request(
    store, tenant, paying, workspace, client, monkeypatch
):
    """Writing a document from nothing is the failure this product exists
    against: fluent, confident, and sourced from the model's training data."""
    async def no_hits(**kwargs):
        return []
    monkeypatch.setattr(store.file_repo, "hybrid_search", no_hits)
    async def fake_embedding(text):
        return QUERY_VEC
    monkeypatch.setattr(drafts_route, "get_text_embedding", fake_embedding, raising=False)

    res = await client.as_(tenant.owner).post(
        "/api/v1/drafts/generate",
        json={"workspace_id": workspace["id"], "prompt": "write a fire safety policy"},
    )
    assert res.status_code == 422
    assert "no documents" in res.json()["detail"].lower()


async def test_a_generated_draft_is_saved_but_not_retrievable(
    store, tenant, paying, workspace, client, monkeypatch
):
    async def fake_embedding(text):
        return QUERY_VEC
    async def fake_generate(prompt, language=None, comprehension_level=None):
        return "# Fire Safety\n\nExtinguishers are checked every 77 months."
    monkeypatch.setattr(drafts_route, "get_text_embedding", fake_embedding, raising=False)
    monkeypatch.setattr(drafts_route, "generate_explanation", fake_generate)

    res = await client.as_(tenant.owner).post(
        "/api/v1/drafts/generate",
        json={"workspace_id": workspace["id"], "prompt": "write a sterilisation SOP"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "draft"
    assert body["sources"], "a draft with no recorded sources cannot be checked"

    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="extinguishers checked every 77 months",
        query_embedding=QUERY_VEC,
        workspace_id=workspace["id"],
    )
    for h in hits:
        assert "77 months" not in (h.get("content") or "")


async def test_staff_cannot_generate(store, tenant, workspace, client):
    member = await tenant.member(scope="workspace", workspaces=[workspace["id"]])
    res = await client.as_(member).post(
        "/api/v1/drafts/generate",
        json={"workspace_id": workspace["id"], "prompt": "write a sterilisation SOP"},
    )
    assert res.status_code == 403


def test_a_title_is_made_from_the_request_without_an_llm_call():
    assert drafts_route._title_from("write a sterilisation SOP") == "Sterilisation SOP"
    assert drafts_route._title_from("Create an onboarding checklist") == "Onboarding checklist"
    assert drafts_route._title_from("") == "Untitled document"


async def test_a_draft_can_be_approved_again_if_the_document_was_deleted(
    store, tenant, paying, draft, client, monkeypatch
):
    """Deleting the approved document must not strand the draft.

    ingested_file_id is ON DELETE SET NULL while status stays 'ingested', so
    reading status alone would refuse a second approval forever and leave a
    draft that says it is in the knowledge base while nothing is.
    """
    async def fake_upload(data, workspace_id, file_id, filename):
        return f"https://storage.example/{file_id}-{filename}"
    monkeypatch.setattr(drafts_route, "upload_bytes_to_gcs", fake_upload)

    first = await client.as_(tenant.owner).post(f"/api/v1/drafts/{draft['id']}/ingest")
    assert first.status_code == 202
    file_id = first.json()["file_id"]

    await store.file_repo.delete_file_entry(file_id)
    orphaned = await store.draft_repo.get(draft["id"])
    assert orphaned["ingested_file_id"] is None, "the FK did not clear"

    again = await client.as_(tenant.owner).post(f"/api/v1/drafts/{draft['id']}/ingest")
    assert again.status_code == 202, again.text
    assert again.json()["file_id"] != file_id
