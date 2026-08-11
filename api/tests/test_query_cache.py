"""Answering the same question twice, for a few minutes.

The saving is obvious and the risk is not: a cache that returns the wrong
answer is worse than no cache, and the ways it could be wrong are all about
what is in the key. So most of what follows is about misses, not hits.

Redis is faked with a dictionary. What is being tested is which key is built
and when, not whether Redis stores strings.
"""
import pytest

from api.core import query_cache

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.expiries = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.expiries[key] = ex

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def get_client():
        return fake

    monkeypatch.setattr(query_cache, "_get_client", get_client)
    monkeypatch.setattr(query_cache, "is_enabled", lambda: True)
    return fake


ASK = dict(
    workspace_id=7,
    question="What is the refund policy?",
    formatted_history="",
    language="English",
    comprehension_level="beginner",
)

ANSWER = {"response": "Thirty days.", "context_chunks": [{"content": "x" * 5000}], "mode": "pipeline"}


async def test_the_same_question_comes_back_without_recomputing():
    assert await query_cache.get(**ASK) is None

    await query_cache.put(result=ANSWER, **ASK)
    hit = await query_cache.get(**ASK)

    assert hit is not None
    assert hit["response"] == "Thirty days."
    assert hit["cached"] is True


async def test_case_and_spacing_alone_do_not_make_a_new_question():
    """True of the question field, and not the whole story: see below."""
    await query_cache.put(result=ANSWER, **ASK)

    hit = await query_cache.get(**{**ASK, "question": "  what is the   REFUND policy? "})

    assert hit is not None


async def test_in_a_real_conversation_the_wording_still_has_to_match():
    """What actually happens in the app, as opposed to the line above.

    The route saves the question before queueing the run, so the conversation
    handed to the worker already contains it verbatim. Retyping the same
    question with different capitals therefore misses, because the raw text
    reaches the key through the history even though the question field was
    normalised. Measured against the running app: identical wording answered in
    0.33s, different capitals took 14.92s.

    Asserted so the next person reads it as a known limit with a known cause,
    rather than filing a bug against the normalisation.
    """
    asked = "What is the refund policy?"
    retyped = "  what IS the   refund policy? "

    await query_cache.put(
        result=ANSWER, **{**ASK, "formatted_history": [{"role": "user", "content": asked}]}
    )

    assert await query_cache.get(
        **{**ASK, "formatted_history": [{"role": "user", "content": asked}]}
    ) is not None
    assert await query_cache.get(
        **{**ASK, "question": retyped,
           "formatted_history": [{"role": "user", "content": retyped}]}
    ) is None


async def test_another_workspace_never_sees_it():
    """The tenant boundary. This is the one that must not regress."""
    await query_cache.put(result=ANSWER, **ASK)

    assert await query_cache.get(**{**ASK, "workspace_id": 8}) is None


async def test_a_follow_up_question_does_not_get_the_wrong_answer():
    """"What about the second one?" means nothing without the conversation."""
    await query_cache.put(result=ANSWER, **ASK)

    assert await query_cache.get(**{**ASK, "formatted_history": "user: something else"}) is None


async def test_a_different_document_scope_is_a_different_question():
    await query_cache.put(result=ANSWER, **ASK)

    assert await query_cache.get(**{**ASK, "file_id": 3}) is None


async def test_language_and_level_change_the_answer_so_they_change_the_key():
    await query_cache.put(result=ANSWER, **ASK)

    assert await query_cache.get(**{**ASK, "language": "French"}) is None
    assert await query_cache.get(**{**ASK, "comprehension_level": "expert"}) is None


async def test_a_new_document_drops_the_cached_answers_at_once():
    """Otherwise: "I just uploaded it and it says it cannot find it."."""
    await query_cache.put(result=ANSWER, **ASK)
    assert await query_cache.get(**ASK) is not None

    await query_cache.bump_document_version(ASK["workspace_id"])

    assert await query_cache.get(**ASK) is None


async def test_nothing_is_cached_without_a_workspace():
    """No single document set, so no safe key."""
    ask = {**ASK, "workspace_id": None}

    await query_cache.put(result=ANSWER, **ask)

    assert await query_cache.get(**ask) is None


async def test_the_retrieved_pages_are_not_stored(fake_redis):
    """They are the bulk of an answer and nothing reads them on a repeat."""
    await query_cache.put(result=ANSWER, **ASK)

    stored = next(v for k, v in fake_redis.store.items() if k.startswith("syntext:answer:"))

    assert "xxxxx" not in stored, "the full text of every retrieved page was cached"
    assert '"context_chunk_count": 1' in stored


async def test_an_error_is_never_cached():
    """A service being briefly down must not become five minutes of failure."""
    await query_cache.put(result={"response": "", "error": "upstream exploded"}, **ASK)
    assert await query_cache.get(**ASK) is None

    await query_cache.put(result={"error": "upstream exploded"}, **ASK)
    assert await query_cache.get(**ASK) is None


async def test_an_answer_expires(fake_redis):
    await query_cache.put(result=ANSWER, **ASK)

    key = next(k for k in fake_redis.store if k.startswith("syntext:answer:"))
    assert fake_redis.expiries[key] == query_cache.TTL_SECONDS
    assert 0 < query_cache.TTL_SECONDS <= 900, "this is a short-lived cache by design"


async def test_a_broken_redis_is_a_miss_not_an_error(monkeypatch):
    """Every failure here has to look like "not cached"."""
    class _Broken:
        async def get(self, key):
            raise RuntimeError("redis is down")

        async def set(self, key, value, ex=None):
            raise RuntimeError("redis is down")

        async def incr(self, key):
            raise RuntimeError("redis is down")

    async def broken_client():
        return _Broken()

    monkeypatch.setattr(query_cache, "_get_client", broken_client)

    assert await query_cache.get(**ASK) is None
    await query_cache.put(result=ANSWER, **ASK)
    await query_cache.bump_document_version(7)


async def test_the_conversation_arrives_as_a_list_not_a_string():
    """The shape the application actually passes.

    format_user_chat_history returns [{role, content}, ...]. The first version
    of this module called .encode() on it, so every read and every write raised,
    was swallowed by the guard, and the cache silently never worked while the
    tests were green. Found by asking the running app the same question twice
    and watching it take just as long.
    """
    history = [{"role": "user", "content": "what about page 2?"}]
    ask = {**ASK, "formatted_history": history}

    await query_cache.put(result=ANSWER, **ask)
    hit = await query_cache.get(**ask)

    assert hit is not None, "a list-shaped history broke the key"
    assert hit["response"] == "Thirty days."

    # And a different conversation still misses.
    other = [{"role": "user", "content": "something else entirely"}]
    assert await query_cache.get(**{**ASK, "formatted_history": other}) is None


async def test_the_same_history_hashes_the_same_either_way_round():
    """Two equal histories built in a different key order are one history."""
    a = [{"role": "user", "content": "hello"}]
    b = [{"content": "hello", "role": "user"}]

    await query_cache.put(result=ANSWER, **{**ASK, "formatted_history": a})

    assert await query_cache.get(**{**ASK, "formatted_history": b}) is not None
