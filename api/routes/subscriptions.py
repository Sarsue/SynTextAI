from flask import Blueprint, request, jsonify, current_app
import stripe
from dotenv import load_dotenv
from utils import decode_firebase_token, get_user_id
from datetime import datetime
import os
import logging
load_dotenv()

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')


price_id = os.getenv('STRIPE_PRICE_ID')
stripe.api_key = os.getenv('STRIPE_SECRET')
endpoint_secret = os.getenv('STRIPE_ENDPOINT_SECRET')
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
        
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401
        
        success, user_info = get_user_id(token)
        if not success:
            return jsonify({'error': 'User authentication failed'}), 401
        
        user_id = get_id_helper(store, success, user_info)
        if not user_id:
            return jsonify({'error': 'User ID not found'}), 404

        subscription = store.get_subscription(user_id)
        
        if not subscription:
            return jsonify({
                'status': 'none',
                'card_last4': None,
                'card_brand': None,
                'card_exp_month': None,
                'card_exp_year': None
            }), 200
        
        # Prepare subscription data to return
        response = {
            'status': subscription['status'],
            'card_last4': subscription.card_last4,
            'card_brand': subscription.card_brand,
            'card_exp_month': subscription.exp_month,
            'card_exp_year': subscription.exp_year
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
        store.update_subscription_status(
            user_id,
            subscription_status['stripe_customer_id'],
            cancellation_result['status']
        )
        card_details = {
                    'card_last4': subscription_status.card_last4,
                    'card_brand':  subscription_status.card.brand,
                    'card_exp_month':  subscription_status.exp_month,
                    'card_exp_year': subscription_status.exp_year
                }
        return jsonify({'subscription_status': cancellation_result['status'], **card_details}), 200

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

        # Check if user already has a subscription
        subscription = store.get_subscription(user_id)
        if subscription:
            # If subscription exists, get the customer ID from it
            stripe_customer_id = subscription.get('stripe_customer_id')
            if subscription.get('status') == 'active':
                logging.error(f"Request came from an already active subscription: {user_id}")
                return jsonify({'error': 'Active subscription already exists'}), 400
        else:
            # If no subscription exists, stripe_customer_id is None
            stripe_customer_id = None
        
        # Retrieve the payment method ID from the request
        payment_method_id = request.json.get('payment_method')
        if not payment_method_id:
            logging.error(f"Request came without a valid payment method ID: {user_id}")
            return jsonify({'error': 'Payment method ID is missing'}), 400

        # If stripe_customer_id is still None, create a new customer
        if not stripe_customer_id:
            customer = stripe.Customer.create(
                description=f"Customer for user_id {user_id}",
                email=user_info.get('email'),  # Use email from user info
                name=user_info.get('name')    # Optional: Use name from user info
            )
            stripe_customer_id = customer.id
            logging.info(f"Created new Stripe customer: {stripe_customer_id}")

        try:
            # Attach the payment method to the customer
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=stripe_customer_id
            )

            # Set the payment method as the default for the customer
            stripe.Customer.modify(
                stripe_customer_id,
                invoice_settings={'default_payment_method': payment_method_id}
            )

            # Create a new Stripe subscription
            subscription = stripe.Subscription.create(
                customer=stripe_customer_id,
                items=[{'price': price_id}],  # Replace with your actual price ID
                default_payment_method=payment_method_id
            )
           
            # Store the subscription in the database
            store.add_or_update_subscription(
                user_id=user_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=subscription.id,
                status=subscription.status,
                current_period_end=datetime.utcfromtimestamp(subscription.current_period_end),
                card_last_4 = payment_method_id.card.last4,
                card_brand = payment_method_id.card.brand,
                exp_month = payment_method_id.card.exp_month,
                exp_year = payment_method_id.card.exp_year
            )
            card_details = {
                    'card_last4': payment_method_id.card.last4,
                    'card_brand':  payment_method_id.card.brand,
                    'card_exp_month':  payment_method_id.card.exp_month,
                    'card_exp_year': payment_method_id.card.exp_year
                }
            return jsonify({'subscription_id': subscription.id, 'message': 'Subscription created successfully', "subscription_status" : subscription.status, **card_details}), 200
        except Exception as e:
            # Card errors like insufficient funds or expired card
            error_msg =  str(e)
            store.add_or_update_subscription(
                user_id=user_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=None,
                status="none",
                current_period_end=None,
            )
            return jsonify({'error': error_msg, 'message': 'Card error occurred'}), 400
        
       


    except Exception as e:
            logging.error(f"Subscription error: {str(e)}")
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

       
        subscription = store.get_subscription(user_id)
        if not subscription:
            return jsonify({'error': 'No subscription found'}), 404

        stripe_customer_id = subscription.get('stripe_customer_id')

        subscription_id = subscription.get('stripe_subscription_id')
        if not subscription_id:
            return jsonify({'error': 'Subscription ID is missing'}), 400

        stripe.PaymentMethod.attach(payment_method, customer=stripe_customer_id)
        stripe.Subscription.modify(subscription_id, default_payment_method=payment_method)

        store.update_subscription(
            stripe_customer_id=stripe_customer_id,
            status=subscription.status,  # Or retrieve status from Stripe if required
            current_period_end=datetime.utcfromtimestamp(subscription.current_period_end),  # Retrieve current period end from Stripe if needed
            card_last_4 = payment_method.card.last4,
            card_brand = payment_method.card.brand,
            exp_month = payment_method.card.exp_month,
            exp_year = payment_method.card.exp_year
        )


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
            store.update_subscription_status(stripe_customer_id, "card_declined")

        elif event_type == 'customer.subscription.updated':
            status = data_object['status']
            current_period_end = data_object['current_period_end']
            store.update_subscription(stripe_customer_id, status, current_period_end)

        elif event_type == 'customer.subscription.deleted':
            store.update_subscription_status(stripe_customer_id, "cancelled")

        else:
            return jsonify({"error": "Unhandled event"}), 400

        return jsonify({"status": "success"}), 200

    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
