"""The knowledge base as a tool, and what it refuses.

Most of this is the same boundary the search route has, arriving through a
different door, so most of it is refusal. The rest is protocol: a client that
gets a malformed envelope back fails in ways nobody can debug from our logs.

The tools deliberately return retrieval rather than answers, so what is asserted
here is the shape and the scoping, never the quality of a generated reply.
"""
import pytest
import pytest_asyncio

from api.routes import mcp as mcp_route

pytestmark = pytest.mark.asyncio(loop_scope="session")

MCP = "/api/v1/mcp"


@pytest_asyncio.fixture(loop_scope="session")
async def paying(store, tenant):
    from api.core.plans import STARTER

    await store.user_repo.add_or_update_subscription(
        user_id=tenant.owner,
        organization_id=tenant.org,
        stripe_customer_id="cus_test_mcp",
        stripe_subscription_id="sub_test_mcp",
        status="active",
        seats=STARTER.included_seats,
        plan_key="starter",
    )
    return tenant


@pytest.fixture
def embedder(monkeypatch):
    async def embed(_text):
        return [0.0] * 1024

    monkeypatch.setattr(mcp_route, "get_text_embedding", embed)


@pytest.fixture
def no_paid_calls(monkeypatch):
    async def embed(_text):
        raise AssertionError("the embedding service was called on a refused request")

    monkeypatch.setattr(mcp_route, "get_text_embedding", embed)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def call(name: str, arguments: dict, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


async def issue(store, workspace_id: int, user_id: int) -> dict:
    return await store.api_key_repo.create_api_key(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name="Claude desktop",
    )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

async def test_initialize_answers_with_a_protocol_version(store, tenant, client):
    workspace = await tenant.workspace("Init")
    key = await issue(store, workspace, tenant.owner)

    response = await client.as_(tenant.owner).post(
        MCP,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers=auth(key["token"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == mcp_route.PROTOCOL_VERSION
    assert body["result"]["serverInfo"]["name"] == mcp_route.SERVER_NAME


async def test_a_notification_gets_no_body(store, tenant, client):
    """`initialized` has no id. Answering it makes a client wait forever."""
    workspace = await tenant.workspace("Notify")
    key = await issue(store, workspace, tenant.owner)

    response = await client.as_(tenant.owner).post(
        MCP,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=auth(key["token"]),
    )

    assert response.status_code == 202
    assert not response.content


async def test_tools_are_listed_with_schemas(store, tenant, client):
    workspace = await tenant.workspace("Tools")
    key = await issue(store, workspace, tenant.owner)

    response = await client.as_(tenant.owner).post(
        MCP,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=auth(key["token"]),
    )

    tools = response.json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"search_knowledge", "get_page", "list_drafts"}
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


async def test_an_unknown_method_is_a_json_rpc_error_not_an_http_one(
    store, tenant, client
):
    workspace = await tenant.workspace("Unknown")
    key = await issue(store, workspace, tenant.owner)

    response = await client.as_(tenant.owner).post(
        MCP,
        json={"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
        headers=auth(key["token"]),
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == mcp_route.METHOD_NOT_FOUND


async def test_an_unknown_tool_is_refused(store, tenant, client):
    workspace = await tenant.workspace("NoTool")
    key = await issue(store, workspace, tenant.owner)

    response = await client.as_(tenant.owner).post(
        MCP, json=call("delete_everything", {}), headers=auth(key["token"])
    )

    assert response.json()["error"]["code"] == mcp_route.INVALID_PARAMS


# ---------------------------------------------------------------------------
# Who may call it
# ---------------------------------------------------------------------------

async def test_a_revoked_key_cannot_call_a_tool(store, paying, client, no_paid_calls):
    workspace = await paying.workspace("Revoked")
    key = await issue(store, workspace, paying.owner)
    await store.api_key_repo.revoke(key["id"], workspace)

    response = await client.as_(paying.owner).post(
        MCP, json=call("search_knowledge", {"query": "anything"}),
        headers=auth(key["token"]),
    )

    assert response.status_code == 401


async def test_removing_the_creator_leaves_the_tool_reaching_nothing(
    store, paying, client, no_paid_calls
):
    """Not an error: the connection is valid and reaches nothing, and the model
    should say so rather than retry."""
    workspace = await paying.workspace("Removed")
    member = await paying.member("analyst", scope="workspace", workspaces=[workspace])
    key = await issue(store, workspace, member)
    await store.org_repo.set_member_access(paying.org, member, "workspace", [])

    response = await client.as_(member).post(
        MCP, json=call("search_knowledge", {"query": "anything"}),
        headers=auth(key["token"]),
    )

    text = response.json()["result"]["content"][0]["text"]
    assert "no longer has access" in text


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------

async def test_search_returns_passages_a_model_can_cite(
    store, paying, client, embedder, monkeypatch
):
    workspace = await paying.workspace("Handbook")

    async def fake_search(**_kwargs):
        return [
            {
                "file_id": 12,
                "file_name": "Handbook 2024.pdf",
                "page_number": 7,
                "content": "Termination requires thirty days of written notice.",
                "meta_data": {},
            }
        ]

    monkeypatch.setattr(store.file_repo, "hybrid_search", fake_search)
    key = await issue(store, workspace, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("search_knowledge", {"query": "termination"}),
        headers=auth(key["token"]),
    )

    text = response.json()["result"]["content"][0]["text"]
    assert "Handbook 2024.pdf" in text
    assert "page 7" in text
    assert "file_id=12" in text
    assert "thirty days" in text


async def test_a_figure_page_carries_its_caution_into_the_passage(
    store, paying, client, embedder, monkeypatch
):
    """The warning a person gets in our own answer has to reach a model that
    has never seen our UI, or an unverified torque value reads as fact."""
    workspace = await paying.workspace("Manual")

    async def fake_search(**_kwargs):
        return [
            {
                "file_id": 3,
                "file_name": "Service manual.pdf",
                "page_number": 41,
                "content": "Torque to 21 Nm.",
                "meta_data": {"vision_unverified_page": "figure page"},
            }
        ]

    monkeypatch.setattr(store.file_repo, "hybrid_search", fake_search)
    key = await issue(store, workspace, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("search_knowledge", {"query": "torque"}),
        headers=auth(key["token"]),
    )

    text = response.json()["result"]["content"][0]["text"]
    assert "READ FROM A FIGURE" in text
    assert "checked against the document" in text


async def test_nothing_found_tells_the_model_not_to_invent(
    store, paying, client, embedder, monkeypatch
):
    workspace = await paying.workspace("Empty")

    async def fake_search(**_kwargs):
        return []

    monkeypatch.setattr(store.file_repo, "hybrid_search", fake_search)
    key = await issue(store, workspace, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("search_knowledge", {"query": "anything"}),
        headers=auth(key["token"]),
    )

    text = response.json()["result"]["content"][0]["text"]
    assert "general knowledge" in text


async def test_get_page_refuses_a_document_in_another_workspace(
    store, paying, client, no_paid_calls
):
    issued_for = await paying.workspace("Mine")
    other = await paying.workspace("Theirs")
    file_id = await store.file_repo.add_file(
        user_id=paying.owner, file_name="salaries.pdf", file_url="", workspace_id=other
    )
    key = await issue(store, issued_for, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("get_page", {"file_id": file_id, "page_number": 1}),
        headers=auth(key["token"]),
    )

    result = response.json()["result"]
    assert result["isError"] is True
    assert "No document with that file_id" in result["content"][0]["text"]


async def test_get_page_needs_numbers(store, paying, client, no_paid_calls):
    workspace = await paying.workspace("Args")
    key = await issue(store, workspace, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("get_page", {"file_id": "twelve", "page_number": 1}),
        headers=auth(key["token"]),
    )

    assert response.json()["result"]["isError"] is True


async def test_a_batch_is_refused_rather_than_half_done(store, tenant, client):
    workspace = await tenant.workspace("Batch")
    key = await issue(store, workspace, tenant.owner)

    response = await client.as_(tenant.owner).post(
        MCP,
        json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        headers=auth(key["token"]),
    )

    assert response.json()["error"]["code"] == mcp_route.INVALID_REQUEST



# ---------------------------------------------------------------------------
# Drafts: visible to this tool, invisible to search, on purpose
# ---------------------------------------------------------------------------
async def test_list_drafts_names_them_without_quoting_them(
    store, paying, client, no_paid_calls
):
    """The tool exists so the model does not rewrite a policy that exists.

    It must not become a way to read one. A generated document that could be
    retrieved would make the model's output the model's source, which is why
    drafts live in their own table and retrieval never joins it. Naming a draft
    is safe; quoting it is the thing that is not.
    """
    workspace = await paying.workspace("Handbook")
    await store.draft_repo.create(
        workspace_id=workspace,
        created_by=paying.owner,
        title="Infection Control SOP",
        prompt="write an infection control SOP",
        content="# Infection Control\n\nWash hands with soap for twenty seconds.",
        sources=[],
    )
    key = await issue(store, workspace, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("list_drafts", {}), headers=auth(key["token"])
    )

    text = response.json()["result"]["content"][0]["text"]
    assert "Infection Control SOP" in text
    assert "not yet in the knowledge base" in text
    assert "Wash hands" not in text, "a draft's words travelled through the tool"


async def test_list_drafts_cannot_see_another_workspace(
    store, paying, client, no_paid_calls
):
    """The same tenant boundary every other tool on this endpoint has."""
    mine = await paying.workspace("Mine")
    theirs = await paying.workspace("Theirs")
    await store.draft_repo.create(
        workspace_id=theirs,
        created_by=paying.owner,
        title="Somebody Elses Policy",
        prompt="write it",
        content="secret",
        sources=[],
    )
    key = await issue(store, mine, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("list_drafts", {}), headers=auth(key["token"])
    )

    text = response.json()["result"]["content"][0]["text"]
    assert "Somebody Elses Policy" not in text


async def test_list_drafts_with_none_says_so(store, paying, client, no_paid_calls):
    """An empty workspace is an answer, not an error."""
    workspace = await paying.workspace("Empty")
    key = await issue(store, workspace, paying.owner)

    response = await client.as_(paying.owner).post(
        MCP, json=call("list_drafts", {}), headers=auth(key["token"])
    )

    body = response.json()
    assert body["result"]["isError"] is False
    assert "not drafted any documents" in body["result"]["content"][0]["text"]
