from flask import Blueprint, request, jsonify, current_app
import stripe
from dotenv import load_dotenv
from utils import decode_firebase_token, get_user_id
from datetime import datetime
import os

load_dotenv()

stripe.api_key = os.getenv('STRIPE_SECRET')
endpoint_secret = os.getenv('STRIPE_ENDPOINT_SECRET')

subscriptions_bp = Blueprint("subscriptions", __name__, url_prefix="/api/v1/subscriptions")

def get_id_helper(store, success, user_info):
    if not success:
        return jsonify(user_info), 401

    email = user_info['email']
    user_id = store.get_user_id_from_email(email)
    return user_id

@subscriptions_bp.route('/status', methods=['GET'])
def subscription_status():
    store = current_app.store
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Authorization token is missing'}), 401

    token = token.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)

    if not success:
        return jsonify(user_info), 401
    
    user_id = get_id_helper(store, success, user_info)
    subscription = store.get_subscription(user_id)
    
    # Check if user has used free trial by comparing trial_end with current time
    free_trial_used = False
    if subscription and subscription.get('trial_end'):
        trial_end = subscription.get('trial_end')
        if trial_end and trial_end < datetime.utcnow():
            free_trial_used = True  # Free trial has ended in the past

    if subscription:
        return jsonify({
            'subscription_status': subscription['status'],
            'free_trial_used': free_trial_used,
            'has_payment_method': subscription.get('default_payment_method') is not None
        }), 200
    else:
        return jsonify({
            'subscription_status': 'none',
            'free_trial_used': free_trial_used,
            'has_payment_method': False
        }), 200

@subscriptions_bp.route('/cancel', methods=['POST'])
def cancel_sub():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401

        token = token.split("Bearer ")[1]
        success, user_info = decode_firebase_token(token)

        if not success:
            return jsonify(user_info), 401
        
        user_id = get_id_helper(store, success, user_info)
        subscription_status = store.get_subscription(user_id)
       
        if not subscription_status:
            return jsonify({'error': 'No subscription found'}), 404
        
        subscription_id = subscription_status.get('stripe_subscription_id')
        if not subscription_id:
            return jsonify({'error': 'Subscription ID is missing'}), 400

        cancellation_result = stripe.Subscription.delete(subscription_id)
        store.add_or_update_subscription(user_id, subscription_status['stripe_customer_id'], subscription_id, cancellation_result['status'])
   
        return jsonify({'subscription_status': cancellation_result['status']}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 403

@subscriptions_bp.route('/start-trial', methods=['POST'])
def start_trial():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401

        token = token.split("Bearer ")[1]
        success, user_info = decode_firebase_token(token)

        if not success:
            print(f"Token decoding failed: {user_info}")  # Log the user_info or error details
            return jsonify(user_info), 401

        user_id = get_id_helper(store, success, user_info)
        subscription = store.get_subscription(user_id)

        if subscription and subscription.get('trial_end') and subscription.get('trial_end') < datetime.utcnow():
            return jsonify({'error': 'Free trial has already been used'}), 400

        payment_method = request.json.get('payment_method')
        if not payment_method:
            return jsonify({'error': 'Payment method is missing'}), 400

        # Log Stripe customer and price details
        print(f"Creating Stripe subscription for customer: {store.get_stripe_customer_id(user_id)}")

        subscription = stripe.Subscription.create(
            customer=store.get_stripe_customer_id(user_id),
            items=[{'price': 'price_1PegHFHuDDTkwuzjucyRQKE1'}],  # Ensure this is valid
            trial_period_days=7,
            default_payment_method=payment_method,
        )

        # Save subscription to the store
        store.add_or_update_subscription(
            user_id=user_id,
            stripe_customer_id=store.get_stripe_customer_id(user_id),
            stripe_subscription_id=subscription.id,
            status=subscription.status,
            trial_end=datetime.utcfromtimestamp(subscription.trial_end) if subscription.trial_end else None,
            current_period_end=datetime.utcfromtimestamp(subscription.current_period_end),
        )

        return jsonify({'subscription_id': subscription.id, 'message': 'Free trial started successfully'}), 200

    except Exception as e:
        print(f"Error occurred: {str(e)}")  # Log the exception for debugging
        return jsonify({'error': str(e)}), 403

@subscriptions_bp.route('/sub', methods=['POST'])
def create_subscription():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401

        token = token.split("Bearer ")[1]
        success, user_info = decode_firebase_token(token)

        if not success:
            return jsonify(user_info), 401

        user_id = get_id_helper(store, success, user_info)
        subscription = store.get_subscription(user_id)

        # Check if free trial has already been used
        if not subscription or (subscription.get('trial_end') and subscription.get('trial_end') >= datetime.utcnow()):
            return jsonify({'error': 'Free trial is still active or not applicable'}), 400

        # Check if payment method is provided
        payment_method = request.json.get('payment_method')
        if not payment_method:
            return jsonify({'error': 'Payment method is missing'}), 400

        # Create regular subscription (without a free trial)
        subscription = stripe.Subscription.create(
            customer=store.get_stripe_customer_id(user_id),
            items=[{'price': 'price_1PegHFHuDDTkwuzjucyRQKE1'}],  # Replace with your actual price ID
            default_payment_method=payment_method,
        )

        # Save subscription to the store
        store.add_or_update_subscription(
            user_id=user_id,
            stripe_customer_id=store.get_stripe_customer_id(user_id),
            stripe_subscription_id=subscription.id,
            status=subscription.status,
            trial_end=datetime.utcfromtimestamp(subscription.trial_end) if subscription.trial_end else None,
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

        token = token.split("Bearer ")[1]
        success, user_info = decode_firebase_token(token)

        if not success:
            return jsonify(user_info), 401

        user_id = get_id_helper(store, success, user_info)
        payment_method = request.json.get('payment_method')
        if not payment_method:
            return jsonify({'error': 'Payment method is missing'}), 400

        # Retrieve the customer and current subscription
        stripe_customer_id = store.get_stripe_customer_id(user_id)
        subscription = store.get_subscription(user_id)

        if not subscription:
            return jsonify({'error': 'No subscription found'}), 404

        subscription_id = subscription.get('stripe_subscription_id')
        if not subscription_id:
            return jsonify({'error': 'Subscription ID is missing'}), 400

        # Attach the new payment method and update the subscription
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

        if event['type'] == 'invoice.payment_succeeded':
            subscription = event['data']['object']['subscription']
            stripe_customer_id = event['data']['object']['customer']
            store.update_subscription_status(stripe_customer_id, "active")

        elif event['type'] == 'invoice.payment_failed':
            subscription = event['data']['object']['subscription']
            stripe_customer_id = event['data']['object']['customer']
            store.update_subscription_status(stripe_customer_id, "card_expired")

        elif event['type'] == 'customer.subscription.updated':
            subscription = event['data']['object']
            stripe_customer_id = subscription['customer']
            status = subscription['status']
            trial_end = subscription['trial_end']
            current_period_end = subscription['current_period_end']
            store.update_subscription(stripe_customer_id, status, trial_end, current_period_end)

        elif event['type'] == 'customer.subscription.deleted':
            subscription = event['data']['object']
            stripe_customer_id = subscription['customer']
            store.update_subscription_status(stripe_customer_id, "canceled")

        else:
            return jsonify({"error": "Unhandled event"}), 400

        return jsonify({"status": "success"}), 200

    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    except ValueError as e:
        return jsonify({"error": "Invalid payload"}), 400
