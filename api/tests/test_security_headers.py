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
import pathlib
import re

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


def _directives(policy: str) -> dict:
    return {d.strip().split(" ")[0]: d.strip() for d in policy.split(";")}


def test_the_csp_allows_the_posthog_hosts_the_sdk_actually_uses():
    """The browser never contacts app.posthog.com. It contacts the two regional
    hosts, and only those have to be here.

    analytics.ts used to set api_host to app.posthog.com, which posthog-js
    accepts and then quietly resolves: config and recorder.js from
    us-assets.i.posthog.com, events from us.i.posthog.com. Only app.posthog.com
    was allowed, so from the day this policy stopped being Report-Only the
    browser blocked every event. Nothing errored server side, the product looked
    healthy, and the dashboard was simply empty. Found 2026-08-26 by reading the
    console of a locally built image, not by anything failing.
    """
    from api.app import _CSP_POLICY

    directives = _directives(_CSP_POLICY)
    assert "https://us.i.posthog.com" in directives["connect-src"], (
        "events and flags are sent here and would be blocked"
    )
    assert "https://us-assets.i.posthog.com" in directives["connect-src"], (
        "the SDK fetches its config here"
    )
    assert "https://us-assets.i.posthog.com" in directives["script-src"], (
        "session recording lazily loads recorder.js from here"
    )


def test_the_csp_allows_whatever_host_the_frontend_actually_posts_events_to():
    """The pair that broke: analytics.ts names a host, the CSP allows a
    different one, and the only symptom is an empty dashboard.

    Read out of the built bundle rather than out of analytics.ts, because the
    bundle is what a browser runs and is the thing shipped in this image. Tests
    run in that image, so /app/frontend/build is always the frontend that will
    be served. A region change (us -> eu) is exactly the edit this catches.
    """
    from api.app import _CSP_POLICY

    root = pathlib.Path(__file__).resolve().parents[2]
    candidates = [
        *(root / "frontend" / "build" / "assets").glob("*.js"),
        root / "frontend" / "src" / "services" / "analytics.ts",
    ]
    hosts = set()
    for path in candidates:
        if not path.exists():
            continue
        hosts.update(
            re.findall(
                r"""api_host\s*:\s*['"`](https://[^'"`]+)['"`]""",
                path.read_text(errors="ignore"),
            )
        )

    assert hosts, (
        "no api_host found in the built frontend or in analytics.ts. Either the "
        "frontend was not built into this image or the SDK is configured some "
        "other way now, and this test is stale rather than passing."
    )

    connect_src = _directives(_CSP_POLICY)["connect-src"]
    for host in sorted(hosts):
        assert host in connect_src, (
            f"the frontend posts events to {host} and the CSP does not allow it, "
            "so the browser will drop every event silently"
        )
