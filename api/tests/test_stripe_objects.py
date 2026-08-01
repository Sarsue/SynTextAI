"""Reading Stripe objects, which is where the billing bugs have lived.

Three separate faults this year came from the same misunderstanding: a
StripeObject looks like a mapping and is not one. `.get` raises AttributeError
instead of returning a default, so:

  - seats.py read item.get("quantity") inside a function that never raises,
    and no seat change ever reached Stripe. Two members, quantity one.
  - the webhook read event['data'].get('previous_attributes'), so every
    customer.subscription.updated returned 500, Stripe retried forever, and a
    subscription going past_due never reached the database. The organization
    kept full access without paying.
  - _period_end read it off the subscription and raised after the customer had
    already been charged.

None of these were caught by a test, because they only fail against a real
Stripe object and every fixture in sight was a plain dict. These use the real
class.
"""
import pytest
from stripe._stripe_object import StripeObject

from api.routes.subscriptions import _read, _period_end


def _obj(payload):
    """A StripeObject exactly as the webhook and the API hand one over."""
    return StripeObject.construct_from(payload, "sk_test_fixture")


def test_stripe_objects_do_not_support_get():
    """The premise. If this ever fails, the workarounds can be removed."""
    obj = _obj({"id": "sub_1", "status": "active"})
    assert obj["status"] == "active"
    with pytest.raises(AttributeError):
        obj.get("status")


def test_read_returns_values_and_defaults_without_raising():
    obj = _obj({"id": "sub_1", "status": "past_due"})
    assert _read(obj, "status") == "past_due"
    assert _read(obj, "absent") is None
    assert _read(obj, "absent", {}) == {}
    # Plain dicts too, since some call sites pass one.
    assert _read({"status": "active"}, "status") == "active"
    # And nothing raises on a value that cannot be subscripted at all.
    assert _read(None, "status") is None


def test_period_end_reads_the_subscription_item():
    """Stripe moved current_period_end onto the item in 2026-06-24."""
    obj = _obj({
        "id": "sub_1",
        "items": {"data": [{"current_period_end": 1785600000}]},
    })
    assert _period_end(obj) is not None


def test_period_end_falls_back_to_the_subscription():
    obj = _obj({"id": "sub_1", "current_period_end": 1785600000, "items": {"data": [{}]}})
    assert _period_end(obj) is not None


def test_period_end_is_none_rather_than_raising():
    """A missing renewal date must not fail a subscription Stripe accepted."""
    assert _period_end(_obj({"id": "sub_1"})) is None
    assert _period_end(_obj({"id": "sub_1", "items": {"data": []}})) is None


def test_the_fields_the_webhook_touches_are_all_reachable():
    """A subscription.updated payload, read the way the handler reads it."""
    event_data = _obj({
        "object": {
            "id": "sub_1",
            "status": "past_due",
            "customer": "cus_1",
            "items": {"data": [{"price": {"id": "price_1"}, "quantity": 3}]},
        },
        "previous_attributes": {"status": "active"},
    })
    data_object = event_data["object"]

    assert data_object["status"] == "past_due"
    assert _read(_read(event_data, "previous_attributes", {}) or {}, "status") == "active"
    assert _read(data_object, "id") == "sub_1"

    items = _read(_read(data_object, "items", {}) or {}, "data") or []
    assert items[0]["price"]["id"] == "price_1"
    assert items[0]["quantity"] == 3


def test_seat_sync_reads_quantity_by_subscript():
    """The exact expression that silently broke every seat change."""
    item = _obj({"id": "si_1", "quantity": 2})
    assert item["quantity"] == 2
    with pytest.raises(AttributeError):
        item.get("quantity")
