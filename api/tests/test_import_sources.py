"""Importing documents from Drive and SharePoint.

An import is another way *in*, not a way *around*. So most of this asserts that
it is held to what an upload is held to: the same permission, the same plan
check, the same duplicate-name rule, the same file types.

The providers are faked. What is being tested is our side of the exchange, not
whether Google returns bytes.
"""
import pytest
import pytest_asyncio

from api.routes import files as files_route
from api.services import connectors

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeConnector:
    """Stands in for Drive. Records the token it was given, so a test can prove
    the token reaches the provider and never reaches anything else."""

    name = "google_drive"

    def __init__(self):
        self.tokens_seen = []
        self.items_seen = []
        self.refuse = None

    async def fetch(self, item_id, access_token):
        self.tokens_seen.append(access_token)
        self.items_seen.append(item_id)
        if self.refuse:
            raise connectors.ImportRefused(self.refuse)
        return connectors.RemoteDocument(
            filename=f"{item_id}.pdf", content=b"%PDF-1.4 pretend document"
        )


@pytest.fixture
def fake_drive(monkeypatch):
    fake = _FakeConnector()
    monkeypatch.setattr(files_route, "get_connector", lambda provider: fake)

    async def fake_store(data, workspace_id, file_id, filename):
        return f"https://storage.googleapis.com/bucket/workspaces/{workspace_id}/{file_id}-{filename}"

    monkeypatch.setattr(files_route, "upload_bytes_to_gcs", fake_store)
    return fake


@pytest_asyncio.fixture(loop_scope="session")
async def paying(store, tenant):
    from api.core.plans import STARTER

    await store.user_repo.add_or_update_subscription(
        user_id=tenant.owner,
        organization_id=tenant.org,
        stripe_customer_id="cus_test_import",
        stripe_subscription_id="sub_test_import",
        status="active",
        seats=STARTER.included_seats,
        plan_key="starter",
    )
    return tenant


def _body(**overrides):
    body = {
        "provider": "google_drive",
        "access_token": "ya29.a-customers-own-short-lived-token",
        "item_ids": ["doc-one"],
    }
    body.update(overrides)
    return body


async def test_somebody_who_cannot_upload_cannot_import(store, tenant, client, fake_drive):
    """The import route must not be a way past the upload permission."""
    workspace = await tenant.workspace("Docs")
    staff = await tenant.member("reader", scope="organization")

    response = await client.as_(staff).post(
        f"/api/v1/files/import?workspace_id={workspace}", json=_body()
    )

    assert response.status_code == 403
    assert fake_drive.items_seen == [], "the provider was called before the check"


async def test_an_outsider_cannot_import_into_another_companys_workspace(
    store, tenant, client, fake_drive
):
    workspace = await tenant.workspace("Private")
    outsider = await tenant.new_user("outsider")

    response = await client.as_(outsider).post(
        f"/api/v1/files/import?workspace_id={workspace}", json=_body()
    )

    assert response.status_code == 403
    assert fake_drive.items_seen == []


async def test_an_unpaid_company_cannot_import(store, tenant, client, fake_drive):
    """Uploading is gated on the subscription, so importing is too."""
    workspace = await tenant.workspace("Docs")

    response = await client.as_(tenant.owner).post(
        f"/api/v1/files/import?workspace_id={workspace}", json=_body()
    )

    assert response.status_code == 402


async def test_an_imported_document_becomes_an_ordinary_file(
    store, tenant, client, paying, fake_drive
):
    """Same row, same workspace, same queue as a drag-and-drop upload.

    Anything special about an imported document would have to be maintained in
    two places forever.
    """
    workspace = await tenant.workspace("Docs")

    response = await client.as_(tenant.owner).post(
        f"/api/v1/files/import?workspace_id={workspace}",
        json=_body(item_ids=["contract", "policy"]),
    )

    assert response.status_code == 202
    body = response.json()
    assert len(body["imported"]) == 2
    assert body["skipped"] == []

    files = await store.file_repo.list_files_in_workspace(workspace) \
        if hasattr(store.file_repo, "list_files_in_workspace") else None
    if files is not None:
        assert len(files) == 2

    # And each one is queued for ingestion, like an upload.
    from sqlalchemy import text as sql

    async with store.agent_run_repo.get_async_session() as session:
        queued = (await session.execute(sql(
            "SELECT count(*) FROM agent_runs WHERE workspace_id = :ws AND run_type = 'ingest_file'"
        ), {"ws": workspace})).scalar()
    assert queued == 2


async def test_the_token_goes_to_the_provider_and_nowhere_else(
    store, tenant, client, paying, fake_drive
):
    """It is a live credential to a customer's Drive.

    Used for the fetch, never persisted. This asserts the obvious half, that it
    reaches the provider; the half that matters is that nothing here writes it
    down, which is why there is no column for it anywhere.
    """
    workspace = await tenant.workspace("Docs")

    await client.as_(tenant.owner).post(
        f"/api/v1/files/import?workspace_id={workspace}", json=_body()
    )

    assert fake_drive.tokens_seen == ["ya29.a-customers-own-short-lived-token"]


async def test_one_bad_document_does_not_lose_the_others(
    store, tenant, client, paying, fake_drive, monkeypatch
):
    """Nine of ten is a better answer than none of ten.

    A customer picks documents and one has been deleted in Drive since. The
    honest outcome is the nine, and a line naming the one that failed.
    """
    workspace = await tenant.workspace("Docs")

    calls = {"n": 0}
    original = fake_drive.fetch

    async def sometimes_fails(item_id, access_token):
        calls["n"] += 1
        if calls["n"] == 2:
            raise connectors.ImportRefused("That document is no longer in Drive.")
        return await original(item_id, access_token)

    monkeypatch.setattr(fake_drive, "fetch", sometimes_fails)

    response = await client.as_(tenant.owner).post(
        f"/api/v1/files/import?workspace_id={workspace}",
        json=_body(item_ids=["good-one", "deleted-one", "good-two"]),
    )

    body = response.json()
    assert len(body["imported"]) == 2
    assert len(body["skipped"]) == 1
    assert "no longer in Drive" in body["skipped"][0]["reason"]


async def test_a_duplicate_name_is_refused_the_same_way_an_upload_is(
    store, tenant, client, paying, fake_drive
):
    """One name, one document, per workspace, however it arrived.

    Two files called invoice.pdf side by side make a citation ambiguous: an
    answer says "invoice.pdf, page 3" and nobody knows which.
    """
    workspace = await tenant.workspace("Docs")
    await store.file_repo.add_file(
        user_id=tenant.owner, file_name="contract.pdf", file_url="", workspace_id=workspace
    )

    response = await client.as_(tenant.owner).post(
        f"/api/v1/files/import?workspace_id={workspace}", json=_body(item_ids=["contract"])
    )

    body = response.json()
    assert body["imported"] == []
    assert "already in this workspace" in body["skipped"][0]["reason"]


async def test_an_unknown_provider_is_refused(store, tenant, client, monkeypatch):
    workspace = await tenant.workspace("Docs")

    response = await client.as_(tenant.owner).post(
        f"/api/v1/files/import?workspace_id={workspace}", json=_body(provider="dropbox")
    )

    assert response.status_code in (400, 403)


async def test_only_readable_file_types_are_accepted():
    """Offering a format the processors cannot read makes the failure the
    customer's to discover, which is the bug this codebase has fixed twice."""
    with pytest.raises(connectors.ImportRefused) as refused:
        connectors._ensure_supported("spreadsheet.xlsx")

    assert ".xlsx" in str(refused.value)

    # And the ones that do work.
    for name in ("policy.pdf", "handbook.docx", "notes.txt", "readme.md"):
        connectors._ensure_supported(name)


async def test_a_document_over_the_limit_is_refused():
    """The uploader's ceiling, so an import is not a way around it."""
    with pytest.raises(connectors.ImportRefused) as refused:
        connectors._ensure_size("huge.pdf", connectors.MAX_IMPORT_BYTES + 1)

    assert "over the" in str(refused.value)


async def test_the_suite_cannot_write_to_real_object_storage():
    """The conftest guard is only worth having if it fires. This is that check.

    Written because the failure it prevents was silent. On 2026-08-31 figure
    storage was added inside the PDF processor, which no existing stub covered
    because every stub replaces upload_bytes_to_gcs at the route that imported
    it. The suite put 76 PNGs into the live customer bucket over an afternoon,
    and the only symptom was one assertion mentioning a storage.googleapis.com
    URL.

    If someone removes the autouse fixture, this fails on the next run rather
    than a month later.
    """
    from api.core.utils import upload_bytes_to_gcs

    # upload_bytes_to_gcs catches its own exceptions and returns None, so the
    # guard shows up as a refusal to produce a URL rather than as a raise.
    result = await upload_bytes_to_gcs(b"not a real file", 1, 1, "guard-check.png")
    assert result is None, "a test just wrote to object storage for real"
