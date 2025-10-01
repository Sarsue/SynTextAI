import React, { useState, useEffect } from 'react';
import { useStripe, useElements, Elements, CardElement } from '@stripe/react-stripe-js';
import type { Stripe } from '@stripe/stripe-js';
import './PaymentView.css';
import { useUserContext } from '../UserContext'; // Importing the context hook
import { User } from 'firebase/auth';
import posthog from '../services/analytics'; // Import PostHog for analytics

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
    const [trialLoading, setTrialLoading] = useState(false);
    const [trialError, setTrialError] = useState<string | null>(null);
    const [trialMessage, setTrialMessage] = useState<string | null>(null);

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

        // Track subscription attempt
        posthog.capture('subscription_attempt', {
            userId: user?.uid,
            email: email
        });

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
                body: JSON.stringify({
                    payment_method_id: paymentMethod?.id,
                    email: email,
                    name: user?.displayName
                }),
            });

            const data = await response.json();
            
            if (!response.ok) {
                console.error("Subscription error response:", data);
                throw new Error(data.detail || 'Failed to complete subscription');
            }

            console.log("Subscription successful:", data);
            
            // Track successful subscription
            posthog.capture('subscription_success', {
                userId: user?.uid,
                email: email,
                subscriptionStatus: data.status || 'active',
                plan: 'standard'
            });

            // Update subscription status in context
            await fetchSubscriptionStatus();
        } catch (error) {
            console.error("Error during subscription:", error);
            const errorMessage = error instanceof Error ? error.message : 'An unknown error occurred';
            setError(errorMessage);
            
            // Track failed subscription
            posthog.capture('subscription_failed', {
                userId: user?.uid,
                email: email,
                error: errorMessage
            });
            
            setError(errorMessage);
        } finally {
            setIsRequestPending(false);
        }
    };

    const handleStartTrial = async () => {
        if (!user) {
            setTrialError('User not authenticated');
            return;
        }

        // Track trial start attempt
        posthog.capture('trial_start_attempt', {
            userId: user.uid,
            email: user.email
        });

        setTrialLoading(true);
        setTrialError(null);
        setTrialMessage('');

        try {
            const response = await fetch('/api/v1/subscriptions/start-trial', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${await user.getIdToken()}`,
                },
                body: JSON.stringify({
                    email: user.email,
                    name: user.displayName
                })
            });

            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.detail || 'Failed to start trial');
            }

            console.log('Trial started successfully:', data);
            
            // Track successful trial start
            posthog.capture('trial_start_success', {
                userId: user.uid,
                email: user.email,
                trialEnd: data.trial_end
            });

            // Update subscription status in context
            await fetchSubscriptionStatus();
            
            // Show success message
            setTrialMessage('Free trial started successfully!');
        } catch (error) {
            console.error('Error starting trial:', error);
            const errorMessage = error instanceof Error ? error.message : 'Failed to start trial';
            setTrialError(errorMessage);
            
            // Track failed trial start
            posthog.capture('trial_start_failed', {
                userId: user.uid,
                email: user.email,
                error: errorMessage
            });
        } finally {
            setTrialLoading(false);
        }
    };

    // Handle Update Payment Method
    const handleUpdatePaymentMethod = async (e: React.FormEvent) => {
        e.preventDefault();
        const cardElement = validateStripeAndCard();
        if (!stripe || !cardElement || !user) {
            setError('Payment system is not ready. Please try again.');
            return;
        }

        // Track payment method update attempt
        posthog.capture('payment_method_update_attempt', {
            userId: user.uid,
            email: user.email
        });
        
        setIsRequestPending(true);
        setError(null);
        
        try {
            // First create a payment method
            const { paymentMethod, error: paymentMethodError } = await stripe.createPaymentMethod({
                type: 'card',
                card: cardElement,
                billing_details: {
                    email: user.email || undefined,
                    name: user.displayName || 'Customer',
                },
            } as any); // Type assertion to handle Stripe types

            if (paymentMethodError || !paymentMethod) {
                throw new Error(paymentMethodError?.message || 'Failed to create payment method');
            }

            console.log("Sending update payment method request...");
            const response = await fetch('/api/v1/subscriptions/update-payment', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${await user.getIdToken()}`,
                },
                body: JSON.stringify({
                    payment_method_id: paymentMethod.id
                }),
            });

            const data = await response.json();
            
            if (!response.ok) {
                console.error("Payment method update error:", data);
                throw new Error(data.detail || 'Failed to update payment method');
            }

            // Track successful payment method update
            posthog.capture('payment_method_update_success', {
                userId: user.uid,
                email: user.email,
                cardBrand: data.card_brand,
                cardLast4: data.card_last4
            });
            
            // Refresh subscription data
            await fetchSubscriptionStatus();
            
            // Show success message
            setError(null);
            alert('Payment method updated successfully!');
        } catch (error) {
            console.error("Error updating payment method:", error);
            
            // Track payment method update error
            posthog.capture('payment_method_update_error', {
                userId: user?.uid,
                email: email,
                error: (error as Error)?.message || 'Unknown error'
            });
            
            setError((error as Error)?.message || 'Failed to update payment method.');
        } finally {
            setIsRequestPending(false);
        }
    };

    // Handle Cancel Subscription
    const handleCancelSubscription = async () => {
        if (!user) {
            setError('User not authenticated');
            return;
        }

        // Confirm cancellation with user
        if (!window.confirm('Are you sure you want to cancel your subscription? You will retain access until the end of your billing period.')) {
            return;
        }

        // Track subscription cancellation attempt
        posthog.capture('subscription_cancel_attempt', {
            userId: user.uid,
            email: user.email,
            subscriptionStatus: subscriptionData?.subscription_status
        });
        
        setIsRequestPending(true);
        setError(null);
        
        try {
            const response = await fetch('/api/v1/subscriptions/cancel', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${await user.getIdToken()}`
                },
            });

            const data = await response.json();
            
            if (!response.ok) {
                console.error("Subscription cancellation error:", data);
                throw new Error(data.detail || 'Failed to cancel subscription');
            }

            // Track successful subscription cancellation
            posthog.capture('subscription_cancel_success', {
                userId: user.uid,
                email: user.email,
                subscriptionStatus: data.status,
                cancelReason: 'user_initiated'
            });
            
            // Refresh subscription data
            await fetchSubscriptionStatus();
            
            // Show success message
            alert('Your subscription has been scheduled for cancellation. You will retain access until the end of your billing period.');
        } catch (error) {
            console.error("Error canceling subscription:", error);
            
            // Track subscription cancellation error
            posthog.capture('subscription_cancel_error', {
                userId: user?.uid,
                email: email,
                subscriptionStatus: subscriptionData?.subscription_status,
                error: (error as Error)?.message || 'Unknown error'
            });
            
            setError((error as Error)?.message || 'Failed to cancel subscription.');
        } finally {
            setIsRequestPending(false);
        }
    };
    console.log("Subscription status:", subscriptionData?.subscription_status);

    // Determine if the card update is required (expired cards, etc.)
    const isCardUpdateRequired = subscriptionData?.subscription_status &&
        !['none', 'active', 'deleted', 'canceled', 'trialing'].includes(subscriptionData.subscription_status);

    // Show loading state while fetching subscription data
    if (subscriptionData === null) {
        return (
            <div className={`PaymentView ${darkMode ? 'dark-mode' : ''}`}>
                <h3>Payment</h3>
                <p>Loading subscription information...</p>
            </div>
        );
    }

    console.log('Subscription data:', subscriptionData);

    return (
        <div className={`PaymentView ${darkMode ? 'dark-mode' : ''}`}>
            <h3>Payment</h3>

            {/* Handle the case where the user needs to update their card */}
            {isCardUpdateRequired ? (
                <>
                    <p>Your payment method needs to be updated due to an expired card or other issue.</p>
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
                </>
            ) : subscriptionData?.subscription_status === 'none' ? (
                // New user eligible for free trial
                <>
                    <p>You have access to a free trial.</p>
                    <button onClick={handleStartTrial} disabled={isRequestPending}>
                        {isRequestPending ? 'Starting Trial...' : 'Start Free Trial'}
                    </button>
                </>
            ) : subscriptionData?.subscription_status === 'trialing' ? (
                // Trial period active, prompt for subscription
                <>
                    <p>Your free trial is active.</p>
                    {subscriptionData?.trial_end && (
                        <p>Trial ends on: {new Date(subscriptionData.trial_end).toLocaleDateString()}</p>
                    )}
                    <button onClick={handleSubscribe} disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Subscribe Now'}
                    </button>
                </>
            ) : subscriptionData?.subscription_status === 'active' ? (
                // Subscription is active, show card details and cancel button
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
            ) : subscriptionData?.subscription_status === 'deleted' || subscriptionData?.subscription_status === 'canceled' ? (
                // User has canceled or deleted subscription, prompt to subscribe again
                <>
                    <p>Your subscription has been deleted or canceled.</p>
                    <button onClick={handleSubscribe} disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Subscribe Again'}
                    </button>
                </>
            ) : (
                // Default case: Show card form for new subscription
                <div>
                    <p>Subscribe to continue using our service</p>
                    <form onSubmit={handleSubscribe}>
                        <div className="card-element-container">
                            <CardElement
                                options={{
                                    style: {
                                        base: {
                                            color: darkMode ? "#ffffff" : "#000000",
                                            backgroundColor: darkMode ? "#333" : "#ffffff",
                                            fontSize: '16px',
                                            '::placeholder': {
                                                color: darkMode ? "#bbbbbb" : "#888888",
                                            },
                                        },
                                        invalid: {
                                            color: '#ff5252',
                                        },
                                    },
                                    hidePostalCode: true,
                                }}
                            />
                        </div>
                        <button 
                            type="submit" 
                            className="subscribe-button"
                            disabled={isRequestPending}
                        >
                            {isRequestPending ? 'Processing...' : 'Subscribe'}
                        </button>
                    </form>
                </div>
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
