import React, { useState, useEffect } from 'react';
import { useStripe, useElements, Elements, CardElement } from '@stripe/react-stripe-js';
import type { Stripe } from '@stripe/stripe-js';
import './PaymentView.css';
import { useUserContext } from '../UserContext'; // Importing the context hook
import { User } from 'firebase/auth';
interface PaymentViewProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null;
    darkMode: boolean;
}

const PaymentView: React.FC<PaymentViewProps> = ({ stripePromise, user, darkMode }) => {
    const { fetchSubscriptionStatus, setSubscriptionStatus, subscriptionData, setSubscriptionData } = useUserContext(); // Getting subscriptionStatus from context
    const stripe = useStripe();
    const elements = useElements();
    const [email, setEmail] = useState(user?.email || '');
    const [clientSecret, setClientSecret] = useState('');
    const [isRequestPending, setIsRequestPending] = useState(false);
    const [error, setError] = useState<string | null>(null);


    // Fetch Subscription Status
    useEffect(() => {
        if (user) fetchSubscriptionStatus();
    }, [user]);



    // Validate Stripe and CardElement
    const validateStripeAndCard = () => {
        const cardElement = elements?.getElement(CardElement);
        if (!stripe || !cardElement) {
            setError('Payment system is unavailable. Please refresh the page.');
            return null;
        }
        return cardElement;
    };

    const handleSubscribe = async (e: React.FormEvent) => {
        e.preventDefault();
        const cardElement = validateStripeAndCard();
        if (!stripe || !cardElement) {
            console.error("Stripe or Card Element is missing.");
            return;
        }

        setIsRequestPending(true);
        setError(null);

        try {
            console.log("Creating Stripe payment method...");
            const { paymentMethod, error: stripeError } = await stripe.createPaymentMethod({
                type: 'card',
                card: cardElement,
                billing_details: {
                    email,
                    name: user?.displayName || 'Unknown User',
                },
            });

            if (stripeError) {
                throw new Error(stripeError.message || 'Payment method creation failed.');
            }

            console.log("Stripe payment method created:", paymentMethod);

            console.log("Sending subscription request...");
            const response = await fetch('/api/v1/subscriptions/subscribe', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${await user?.getIdToken()}`,
                },
                body: JSON.stringify({ payment_method: paymentMethod?.id }),
            });

            if (!response.ok) {
                const errorResponse = await response.json();
                console.error("Server error response:", errorResponse);
                throw new Error(errorResponse?.error || 'Failed to complete subscription.');
            }

            const data = await response.json();
            console.log("Subscription successful:", data);

            setSubscriptionData(data);
            setSubscriptionStatus(data.subscription_status); // Update context with the new subscription status
        } catch (error) {
            console.error("Error during subscription:", error);
            setError((error as Error)?.message || 'An error occurred while subscribing.');
        } finally {
            setIsRequestPending(false);
        }
    };

    // Handle Update Payment Method
    const handleUpdatePaymentMethod = async (e: React.FormEvent) => {
        e.preventDefault();
        const cardElement = validateStripeAndCard();
        if (!cardElement || !clientSecret || !stripe) return;

        setIsRequestPending(true);
        setError(null);
        try {
            const { setupIntent, error: stripeError } = await stripe.confirmCardSetup(clientSecret, {
                payment_method: {
                    card: cardElement,
                    billing_details: { email },
                },
            });
            if (stripeError) throw new Error(stripeError.message || 'Failed to update payment method.');

            const response = await fetch('/api/v1/subscriptions/update-payment', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${await user?.getIdToken()}`,
                },
                body: JSON.stringify({ payment_method_id: setupIntent?.payment_method }),
            });
            if (!response.ok) throw new Error('Failed to update payment details.');
            const data = await response.json();
            setSubscriptionData(data);
            setSubscriptionStatus(data.subscription_status); // Update context with new subscription status
        } catch (error) {
            setError((error as Error)?.message || 'An error occurred while updating payment method.');
        } finally {
            setIsRequestPending(false);
        }
    };

    // Handle Cancel Subscription
    const handleCancelSubscription = async () => {
        setIsRequestPending(true);
        setError(null);
        try {
            const token = await user?.getIdToken();
            const res = await fetch('/api/v1/subscriptions/cancel', {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!res.ok) throw new Error('Failed to cancel subscription.');
            const data = await res.json();

            setSubscriptionData({ ...subscriptionData, subscription_status: data.subscription_status });
            setSubscriptionStatus(data.subscription_status); // Update context with new subscription status
        } catch (error) {
            setError((error as Error)?.message || 'Failed to cancel subscription.');
        } finally {
            setIsRequestPending(false);
        }
    };

    console.log("Subscription status:", subscriptionData?.subscription_status);
    const isCardUpdateRequired = subscriptionData?.subscription_status
        ? !['canceled', 'active', 'none'].includes(subscriptionData.subscription_status)
        : true;

    return (
        <div className={`PaymentView ${darkMode ? 'dark-mode' : ''}`}>
            <h3>Payment</h3>
            {isCardUpdateRequired ? (
                <form onSubmit={handleUpdatePaymentMethod}>
                    <CardElement
                        options={{
                            style: {
                                base: {
                                    color: darkMode ? "#ffffff" : "#000000", // White in dark mode
                                    backgroundColor: darkMode ? "#333" : "#ffffff",
                                    "::placeholder": {
                                        color: darkMode ? "#bbbbbb" : "#888888", // Adjust placeholder color
                                    },
                                },
                            },
                        }}
                    />

                    <button type="submit" disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Update Payment Method'}
                    </button>
                </form>
            ) : subscriptionData?.subscription_status === 'active' ? (
                <>
                    <p>Your subscription is active.</p>
                    {subscriptionData?.card_last4 && subscriptionData?.card_brand && (
                        <div className="CardDetails">
                            <p>
                                Card: {subscriptionData.card_brand.toUpperCase()} ending in{' '}
                                {subscriptionData.card_last4}
                            </p>
                            <p>
                                Expiry: {subscriptionData.card_exp_month}/{subscriptionData.card_exp_year}
                            </p>
                        </div>
                    )}
                    <button onClick={handleCancelSubscription} disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Cancel Subscription'}
                    </button>
                </>
            ) : (
                <form onSubmit={handleSubscribe}>
                    <CardElement
                        options={{
                            style: {
                                base: {
                                    color: darkMode ? "#ffffff" : "#000000", // White in dark mode
                                    backgroundColor: darkMode ? "#333" : "#ffffff",
                                    "::placeholder": {
                                        color: darkMode ? "#bbbbbb" : "#888888", // Adjust placeholder color
                                    },
                                },
                            },
                        }}
                    />

                    <button type="submit" disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Subscribe'}
                    </button>
                </form>
            )}
            {error && <p className="error">{error}</p>}
        </div>
    );
};

const WrappedPaymentView: React.FC<PaymentViewProps> = (props) => (
    <Elements stripe={props.stripePromise}>
        <PaymentView {...props} />
    </Elements>
);

export default WrappedPaymentView;
