from flask import Blueprint, request, jsonify, current_app
import stripe
from dotenv import load_dotenv
from utils import decode_firebase_token, get_user_id
from datetime import datetime
import os

load_dotenv()

stripe.api_key = os.getenv('STRIPE_SECRET')
endpoint_secret = os.getenv('STRIPE_ENDPOINT_SECRET')
price_id = os.getenv('STRIPE_PRICE_ID')
subscriptions_bp = Blueprint("subscriptions", __name__, url_prefix="/api/v1/subscriptions")

def get_id_helper(store, success, user_info):
    if not success:
        return jsonify(user_info), 401

    # Now you can use the user_info dictionary to allow or restrict actions
    name = user_info['name']
    email = user_info['email']
    id = store.get_user_id_from_email(email)
    return id

@subscriptions_bp.route('/status', methods=['GET'])
def subscription_status():
    try:

        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        user_id = get_id_helper(store, success, user_info)
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401
        subscription = store.get_subscription(user_id)
        response = {
            'subscription_status': subscription['status'] if subscription else 'none',
            'has_payment_method': subscription.get('default_payment_method') is not None if subscription else False
        }
        return jsonify(response), 200
    except Exception as e:
        current_app.logger.error(f"Error in subscription_status: {str(e)}")
        return jsonify({'error': 'An internal error occurred'}), 500


@subscriptions_bp.route('/cancel', methods=['POST'])
def cancel_sub():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401
        success, user_info = get_user_id(token)
        user_id = get_id_helper(store, success, user_info)
        subscription_status = store.get_subscription(user_id)
        if not subscription_status:
            return jsonify({'error': 'No subscription found'}), 404

        subscription_id = subscription_status.get('stripe_subscription_id')
        if not subscription_id:
            return jsonify({'error': 'Subscription ID is missing'}), 400

        cancellation_result = stripe.Subscription.delete(subscription_id)
        store.add_or_update_subscription(
            user_id,
            subscription_status['stripe_customer_id'],
            subscription_id,
            cancellation_result['status']
        )

        return jsonify({'subscription_status': cancellation_result['status']}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 403

@subscriptions_bp.route('/subscribe', methods=['POST'])
def create_subscription():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401
        success, user_info = get_user_id(token)
        user_id = get_id_helper(store, success, user_info)

        subscription = store.get_subscription(user_id)
        if subscription and subscription.get('status') == 'active':
            return jsonify({'error': 'Active subscription already exists'}), 400

        payment_method = request.json.get('payment_method')
        if not payment_method:
            return jsonify({'error': 'Payment method is missing'}), 400

        subscription = stripe.Subscription.create(
            customer=store.get_stripe_customer_id(user_id),
            items=[{'price': price_id}],  # Replace with your actual price ID
            default_payment_method=payment_method,
        )

        store.add_or_update_subscription(
            user_id=user_id,
            stripe_customer_id=store.get_stripe_customer_id(user_id),
            stripe_subscription_id=subscription.id,
            status=subscription.status,
            current_period_end=datetime.utcfromtimestamp(subscription.current_period_end),
        )

        return jsonify({'subscription_id': subscription.id, 'message': 'Subscription created successfully'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 403

@subscriptions_bp.route('/update-payment', methods=['POST'])
def update_payment():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401
        success, user_info = get_user_id(token)
        user_id = get_id_helper(store, success, user_info)

        payment_method = request.json.get('payment_method')
        if not payment_method:
            return jsonify({'error': 'Payment method is missing'}), 400

        stripe_customer_id = store.get_stripe_customer_id(user_id)
        subscription = store.get_subscription(user_id)

        if not subscription:
            return jsonify({'error': 'No subscription found'}), 404

        subscription_id = subscription.get('stripe_subscription_id')
        if not subscription_id:
            return jsonify({'error': 'Subscription ID is missing'}), 400

        stripe.PaymentMethod.attach(payment_method, customer=stripe_customer_id)
        stripe.Subscription.modify(subscription_id, default_payment_method=payment_method)

        return jsonify({'success': True}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@subscriptions_bp.route('/webhook', methods=['POST'])
def webhook():
    store = current_app.store
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)

        event_type = event['type']
        data_object = event['data']['object']
        stripe_customer_id = data_object['customer']

        if event_type == 'invoice.payment_succeeded':
            store.update_subscription_status(stripe_customer_id, "active")

        elif event_type == 'invoice.payment_failed':
            store.update_subscription_status(stripe_customer_id, "card_expired")

        elif event_type == 'customer.subscription.updated':
            status = data_object['status']
            current_period_end = data_object['current_period_end']
            store.update_subscription(stripe_customer_id, status, current_period_end)

        elif event_type == 'customer.subscription.deleted':
            store.update_subscription_status(stripe_customer_id, "canceled")

        else:
            return jsonify({"error": "Unhandled event"}), 400

        return jsonify({"status": "success"}), 200

    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
