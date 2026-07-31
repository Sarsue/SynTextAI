import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStripe, useElements, Elements, CardElement } from '@stripe/react-stripe-js';
import type { Stripe } from '@stripe/stripe-js';
import './PaymentView.css';
import { useUserContext } from '../UserContext'; // Importing the context hook
import { User } from 'firebase/auth';
import posthog from '../services/analytics'; // Import PostHog for analytics
import { Button } from '@/components/ui/button';

interface PaymentViewProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null;
    darkMode: boolean;
}

interface PlanOption {
    key: string;
    name: string;
    description: string;
    base_cents: number;
    included_seats: number;
    overage_cents: number;
    available: boolean;
}

const PaymentView: React.FC<PaymentViewProps> = ({ stripePromise, user, darkMode }) => {
    const { fetchSubscriptionStatus, setSubscriptionStatus, subscriptionData, setSubscriptionData } = useUserContext(); // Getting subscriptionStatus from context
    const navigate = useNavigate();
    const stripe = useStripe();
    const elements = useElements();
    const [email, setEmail] = useState(user?.email || '');
    const [clientSecret, setClientSecret] = useState('');
    const [isRequestPending, setIsRequestPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [plans, setPlans] = useState<PlanOption[]>([]);
    const [selectedPlan, setSelectedPlan] = useState<string>('starter');

    // Fetch Subscription Status
    useEffect(() => {
        if (user) fetchSubscriptionStatus();
    }, [user]);

    // Prices come from the backend, which derives them from the same plan
    // definition the Stripe prices were created from. Hardcoding them here is
    // how a page ends up advertising a price the customer is not charged.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch('/api/v1/subscriptions/plans');
                if (!response.ok) return;
                const data = await response.json();
                if (cancelled || !Array.isArray(data?.plans)) return;
                const available = data.plans.filter((p: PlanOption) => p.available);
                setPlans(available);
                if (available.length) setSelectedPlan(available[0].key);
            } catch {
                // Leaves the picker empty and the subscribe button disabled,
                // which is better than offering a plan we cannot charge for.
            }
        })();
        return () => { cancelled = true; };
    }, []);

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
                body: JSON.stringify({ payment_method: paymentMethod?.id, plan: selectedPlan }),
            });

            if (!response.ok) {
                const errorResponse = await response.json();
                console.error("Server error response:", errorResponse);
                // FastAPI returns {"detail": ...}, never {"error": ...}, so
                // reading .error discarded every real message and replaced it
                // with the generic one — including 'your card was declined'
                // and the reason a subscription actually failed.
                const detail = errorResponse?.detail ?? errorResponse?.error;
                throw new Error(
                    typeof detail === 'string' ? detail
                        : detail?.message ?? 'Failed to complete subscription.'
                );
            }

            const data = await response.json();
            console.log("Subscription successful:", data);
            
            // Track successful subscription
            posthog.capture('subscription_success', {
                userId: user?.uid,
                email: email,
                subscriptionStatus: data.subscription_status,
                plan: selectedPlan
            });

            setSubscriptionData(data);
            setSubscriptionStatus(data.subscription_status);
            // Entitlement lives on the organization, and /chat is gated on it.
            // Setting local state alone left the organization looking unpaid, so
            // navigating bounced straight back here. Re-resolve before leaving.
            await fetchSubscriptionStatus();
            navigate('/chat');
        } catch (error) {
            console.error("Error during subscription:", error);
            
            // Track subscription error
            posthog.capture('subscription_error', {
                userId: user?.uid,
                email: email,
                error: (error as Error)?.message || 'Unknown error'
            });
            
            setError((error as Error)?.message || 'An error occurred while subscribing.');
        } finally {
            setIsRequestPending(false);
        }
    };

    // Handle Update Payment Method
    const handleUpdatePaymentMethod = async (e: React.FormEvent) => {
        e.preventDefault();
        const cardElement = validateStripeAndCard();
        if (!stripe || !cardElement) return;

        // Track payment method update attempt
        posthog.capture('payment_method_update_attempt', {
            userId: user?.uid,
            email: email
        });
        
        setIsRequestPending(true);
        setError(null);
        try {
            const { setupIntent, error: stripeError } = await stripe.confirmCardSetup(clientSecret, {
                payment_method: {
                    card: cardElement,
                    billing_details: { email },
                },
            });

            if (stripeError) {
                throw new Error(stripeError.message || 'Failed to update payment method.');
            }

            console.log("Sending update payment method request...");
            const response = await fetch('/api/v1/subscriptions/update-payment', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${await user?.getIdToken()}`,
                },
                body: JSON.stringify({ payment_method_id: setupIntent?.payment_method }),
            });

            if (!response.ok) {
                throw new Error('Failed to update payment details.');
            }

            const data = await response.json();
            // Track successful payment method update
            posthog.capture('payment_method_update_success', {
                userId: user?.uid,
                email: email,
                cardBrand: data.card_brand,
                cardLast4: data.card_last4
            });
            
            setSubscriptionData(data);
            setSubscriptionStatus(data.subscription_status);
            await fetchSubscriptionStatus();
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
        // Track subscription cancellation attempt
        posthog.capture('subscription_cancel_attempt', {
            userId: user?.uid,
            email: email,
            subscriptionStatus: subscriptionData?.subscription_status
        });
        
        setIsRequestPending(true);
        setError(null);
        try {
            const token = await user?.getIdToken();
            const res = await fetch('/api/v1/subscriptions/cancel', {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
            });

            if (!res.ok) {
                throw new Error('Failed to cancel subscription.');
            }

            const data = await res.json();
            // Track successful subscription cancellation
            posthog.capture('subscription_cancel_success', {
                userId: user?.uid,
                email: email,
                subscriptionStatus: subscriptionData?.subscription_status,
                cancelReason: 'user_initiated'
            });
            
            setSubscriptionData({ ...subscriptionData, subscription_status: data.subscription_status });
            setSubscriptionStatus(data.subscription_status);
            await fetchSubscriptionStatus();
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
                        <Button type="submit" disabled={isRequestPending}>
                            {isRequestPending ? 'Processing...' : 'Update Payment Method'}
                        </Button>
                    </form>
                </>
            ) : subscriptionData?.subscription_status === 'none' ? (
                // No trial to offer. Access is bought here, or granted by being
                // invited into an organization that already pays.
                <>
                    <div className="plan-choices">
                        {plans.map((plan) => {
                            const isSelected = plan.key === selectedPlan;
                            return (
                                <button
                                    type="button"
                                    key={plan.key}
                                    onClick={() => setSelectedPlan(plan.key)}
                                    disabled={!plan.available}
                                    aria-pressed={isSelected}
                                    className={`plan-choice ${isSelected ? 'is-selected' : ''}`}
                                >
                                    <span className="plan-choice-name">{plan.name}</span>
                                    <span className="plan-choice-price">
                                        ${(plan.base_cents / 100).toFixed(0)}
                                        <span className="plan-choice-period">/month</span>
                                    </span>
                                    <span className="plan-choice-seats">
                                        {plan.included_seats} seats included, then $
                                        {(plan.overage_cents / 100).toFixed(0)} each
                                    </span>
                                    <span className="plan-choice-description">{plan.description}</span>
                                </button>
                            );
                        })}
                    </div>

                    <form onSubmit={handleSubscribe}>
                        <CardElement
                            options={{
                                style: {
                                    base: {
                                        color: darkMode ? "#ffffff" : "#000000",
                                        backgroundColor: darkMode ? "#333" : "#ffffff",
                                        "::placeholder": {
                                            color: darkMode ? "#bbbbbb" : "#888888",
                                        },
                                    },
                                },
                            }}
                        />
                        <Button type="submit" disabled={isRequestPending || !plans.length}>
                            {isRequestPending ? 'Processing...' : 'Subscribe'}
                        </Button>
                    </form>
                    <p className="billing-note">
                        Billed monthly. Seats beyond the included allowance are added to your next
                        invoice as you invite people, and removed as soon as you remove them.
                    </p>
                </>
            ) : subscriptionData?.subscription_status === 'active' ? (
                // Subscription is active, show card details and cancel button
                <>
                    <p>Your subscription is active.</p>
                    {subscriptionData?.current_period_end && (
                        <p>
                            Renews on: {new Date(subscriptionData.current_period_end).toLocaleDateString()}.
                        </p>
                    )}
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
                    <Button variant="outline" onClick={handleCancelSubscription} disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Cancel Subscription'}
                    </Button>
                </>
            ) : subscriptionData?.subscription_status === 'deleted' || subscriptionData?.subscription_status === 'canceled' ? (
                // User has canceled or deleted subscription, prompt to subscribe again
                <>
                    <p>Your subscription has been deleted or canceled.</p>
                    <Button onClick={handleSubscribe} disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Subscribe Again'}
                    </Button>
                </>
            ) : (
                // Default case: Prompt to subscribe if no status matches
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
                    <Button type="submit" disabled={isRequestPending}>
                        {isRequestPending ? 'Processing...' : 'Subscribe'}
                    </Button>
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
