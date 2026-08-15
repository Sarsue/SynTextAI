"""The Content-Security-Policy, now that it actually blocks things.

WHY THIS EXISTS NOW AND NOT BEFORE

The policy shipped in Report-Only mode, which sounds like an observation period
and was not one: it carried no report-uri, so every violation went to one user's
devtools console and nowhere else. Nobody was ever going to read those, so the
policy sat advisory indefinitely while the gap it was meant to close stayed open.

Enforcing it changes the failure mode. A missing origin used to cost nothing;
now it silently breaks a feature in a customer's browser — a citation iframe
that stays blank, a checkout that will not open. These tests pin the origins
that carry those features, because the cost of losing one is no longer zero.

WHAT IS ASSERTED

Not the whole policy string, which will keep changing. The four origins whose
absence breaks something a customer paid for, plus the reporting path that is
now the only way we would find out.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _headers(client):
    # These endpoints need no identity; as_(None) just unwraps the HTTP client.
    r = await client.as_(None).get("/health")
    return r.headers


async def test_the_policy_is_enforcing_not_advisory(client):
    """Report-Only is the state this spent weeks in while reporting to nobody."""
    h = await _headers(client)
    assert "content-security-policy" in {k.lower() for k in h}, "no CSP at all"
    assert "content-security-policy-report-only" not in {k.lower() for k in h}, (
        "still advisory; a missing origin costs nothing and blocks nothing"
    )


async def test_violations_have_somewhere_to_go(client):
    """Without report-uri, enforcement breaks features and tells no one."""
    h = await _headers(client)
    assert "report-uri" in h["content-security-policy"]


@pytest.mark.parametrize("origin,feature", [
    ("https://storage.googleapis.com", "citation iframes open the document from GCS"),
    ("https://docs.google.com", "the Drive picker renders in an iframe from here"),
    ("https://js.stripe.com", "checkout"),
    ("https://accounts.google.com", "the token client that asks for drive.file"),
])
async def test_the_origins_a_feature_depends_on(client, origin, feature):
    """Each of these, removed, breaks the named feature in production only.

    Every one is a frame or script source that development never exercises the
    same way, which is exactly the class of mistake enforcement turns from
    harmless into customer-visible.
    """
    policy = (await _headers(client))["content-security-policy"]
    assert origin in policy, f"{origin} missing: {feature} would break"


async def test_a_malformed_report_is_not_an_incident(client):
    """The browser posts these unauthenticated and we do not control the body.

    Anything other than a quiet 204 on rubbish would turn a violation report
    into a second problem, and the endpoint is public so anyone can send one.
    """
    for body in [b"", b"not json", b"{}", b'{"csp-report":{}}', b'{"csp-report":null}']:
        r = await client.as_(None).post(
            "/api/csp-report", content=body, headers={"Content-Type": "application/csp-report"}
        )
        assert r.status_code == 204, f"{body!r} returned {r.status_code}"


async def test_a_real_report_is_accepted(client):
    r = await client.as_(None).post(
        "/api/csp-report",
        json={"csp-report": {
            "violated-directive": "script-src",
            "blocked-uri": "https://evil.example/x.js",
            "document-uri": "https://syntextai.com/",
        }},
    )
    assert r.status_code == 204


async def test_an_oversized_report_cannot_flood_the_logs(client):
    """The body is attacker-controlled and the endpoint is public, so the
    fields are truncated rather than logged whole."""
    r = await client.as_(None).post(
        "/api/csp-report",
        json={"csp-report": {
            "violated-directive": "script-src" + "A" * 5000,
            "blocked-uri": "https://evil.example/" + "B" * 20000,
            "document-uri": "C" * 20000,
        }},
    )
    assert r.status_code == 204


async def test_the_other_headers_are_still_there(client):
    """Enforcement changed one header; these three must not have moved."""
    h = await _headers(client)
    assert h.get("x-frame-options") == "DENY"
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("referrer-policy") == "strict-origin-when-cross-origin"
