"""Replacing the card on a subscription that has fallen behind.

WHY THIS FILE EXISTS

This is the recovery path for a paying customer who is locked out. Since
2026-08-07 a past_due organization cannot ask questions either, so this form is
the whole way back in, and until that same day it had never once worked. Three
independent defects, any one of them fatal:

  1. The browser called confirmCardSetup(clientSecret) with a state that was
     declared and never assigned, and no endpoint existed to supply one.
  2. It sent `payment_method_id`; the route reads `payment_method`. FastAPI
     rejected the request with a 422 before the handler ran.
  3. The route rebound `payment_method` to the retrieved PaymentMethod object
     and then passed that object where the API takes an id.

Nobody noticed because nobody reaches this screen until their card fails.

WHY THESE HIT STRIPE INSTEAD OF A MOCK

Defect 3 is the argument. A mocked `PaymentMethod.attach_async` accepts an
object as happily as an id, so a mocked version of this test would have passed
green for months while the real thing was broken. Same reasoning the rest of
this suite uses for running against a real Postgres: a mock proves the mock.

The cost is a few seconds and a handful of test-mode objects, all removed in
teardown. The guard below refuses to run against anything but a test key, so a
misconfigured environment skips instead of creating customers in live mode.
"""
import os
import uuid

import pytest
import pytest_asyncio
import stripe

pytestmark = pytest.mark.asyncio(loop_scope="session")

_KEY = os.getenv("STRIPE_SECRET") or ""
_PRICE = os.getenv("STRIPE_PRICE_ID_STARTER") or ""

# Never live. A test that creates customers and subscriptions must not be one
# `.env` mixup away from doing it on the real account.
pytestmark = [
    pytestmark,
    pytest.mark.skipif(
        not _KEY.startswith("sk_test"),
        reason="needs a Stripe test key (STRIPE_SECRET=sk_test...)",
    ),
    pytest.mark.skipif(not _PRICE, reason="needs STRIPE_PRICE_ID_STARTER"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def past_due_customer(store, tenant):
    """A real test-mode customer and subscription, recorded like a live one.

    Built through Stripe rather than written straight into the table because
    the point of these tests is what happens on the Stripe side of the call.
    """
    stripe.api_key = _KEY
    tag = uuid.uuid4().hex[:8]

    customer = stripe.Customer.create(
        description=f"card-update test {tag}",
        metadata={"test": "card_update", "organization_id": str(tenant.org)},
    )
    original = stripe.PaymentMethod.create(type="card", card={"token": "tok_visa"})
    stripe.PaymentMethod.attach(original.id, customer=customer.id)
    stripe.Customer.modify(
        customer.id, invoice_settings={"default_payment_method": original.id}
    )
    subscription = stripe.Subscription.create(
        customer=customer.id,
        items=[{"price": _PRICE, "quantity": 1}],
        default_payment_method=original.id,
    )

    # past_due is the state that sends somebody to this form.
    await store.user_repo.add_or_update_subscription(
        user_id=tenant.owner,
        organization_id=tenant.org,
        stripe_customer_id=customer.id,
        stripe_subscription_id=subscription.id,
        status="past_due",
        card_last4=original.card.last4,
        card_type=original.card.brand,
        exp_month=original.card.exp_month,
        exp_year=original.card.exp_year,
    )

    class Fixture:
        customer_id = customer.id
        subscription_id = subscription.id
        original_last4 = original.card.last4

    yield Fixture

    try:
        stripe.Subscription.cancel(subscription.id)
    except Exception:
        pass
    try:
        stripe.Customer.delete(customer.id)
    except Exception:
        pass


async def test_setup_intent_gives_the_browser_a_secret_to_confirm_against(
    tenant, client, past_due_customer
):
    """confirmCardSetup needs a secret, and nothing used to produce one.

    The card-update form called it with an empty string, so it failed on
    contact. This endpoint is the missing half.
    """
    res = await client.as_(tenant.owner).post("/api/v1/subscriptions/setup-intent")

    assert res.status_code == 200
    secret = res.json()["client_secret"]
    assert secret and secret.startswith("seti_"), secret


async def test_setup_intent_is_refused_without_a_subscription(tenant, client):
    """No billing account, nothing to attach a card to."""
    res = await client.as_(tenant.owner).post("/api/v1/subscriptions/setup-intent")

    assert res.status_code == 404


async def test_update_payment_reads_the_field_the_browser_actually_sends(
    tenant, client, past_due_customer
):
    """Pins the request contract, which is where defect 2 lived.

    The browser sent `payment_method_id` and the route reads `payment_method`,
    so every request 422'd before the handler ran. Renaming either side now
    fails here instead of silently in production.
    """
    wrong = await client.as_(tenant.owner).post(
        "/api/v1/subscriptions/update-payment", json={"payment_method_id": "pm_card_visa"}
    )
    assert wrong.status_code == 422


async def test_updating_the_card_moves_it_in_stripe_and_in_our_row(
    store, tenant, client, past_due_customer
):
    """The whole path, against Stripe.

    Asserts on the customer's default as well as the subscription's. Setting
    only the subscription leaves the next invoice retrying the card that just
    failed, so the customer updates their card and is charged on the old one
    anyway.
    """
    stripe.api_key = _KEY
    replacement = stripe.PaymentMethod.create(
        type="card", card={"token": "tok_mastercard"}
    )
    assert replacement.card.last4 != past_due_customer.original_last4

    res = await client.as_(tenant.owner).post(
        "/api/v1/subscriptions/update-payment",
        json={"payment_method": replacement.id},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["card_last4"] == replacement.card.last4
    assert body["card_brand"] == replacement.card.brand

    # Stripe is the fact. Both defaults, for the reason in the docstring.
    subscription = stripe.Subscription.retrieve(past_due_customer.subscription_id)
    assert subscription.default_payment_method == replacement.id

    customer = stripe.Customer.retrieve(past_due_customer.customer_id)
    assert customer["invoice_settings"]["default_payment_method"] == replacement.id

    # And our row, which is what the settings screen renders from.
    subscription_row, card = await store.user_repo.get_subscription(tenant.owner)
    assert card["card_last4"] == replacement.card.last4
    # Read back from Stripe rather than echoed from the row we had, so paying
    # the outstanding invoice is reflected instead of leaving it past_due.
    assert subscription_row["status"] == subscription.status
