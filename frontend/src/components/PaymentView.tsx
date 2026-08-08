import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStripe, useElements, Elements, CardElement } from '@stripe/react-stripe-js';
import type { Stripe } from '@stripe/stripe-js';
import './PaymentView.css';
import { useUserContext } from '../UserContext'; // Importing the context hook
import { User } from 'firebase/auth';
import posthog from '../services/analytics'; // Import PostHog for analytics
import { subscribeWithCard } from '../services/subscribe';
import { PlanChoices, usePlans } from './PlanPicker';
import { Button } from '@/components/ui/button';

interface PaymentViewProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null;
    darkMode: boolean;
}

const PaymentView: React.FC<PaymentViewProps> = ({ stripePromise, user, darkMode }) => {
    const { fetchSubscriptionStatus, setSubscriptionStatus, subscriptionData, setSubscriptionData, activeOrganizationId } = useUserContext(); // Getting subscriptionStatus from context
    const navigate = useNavigate();
    const stripe = useStripe();
    const elements = useElements();
    const [email, setEmail] = useState(user?.email || '');
    const [isRequestPending, setIsRequestPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [selectedPlan, setSelectedPlan] = useState<string>('starter');

    // Fetch Subscription Status
    useEffect(() => {
        if (user) fetchSubscriptionStatus();
    }, [user]);

    const plans = usePlans(setSelectedPlan);

    // Who still has to pay. Anything that is not a live subscription lands on
    // the subscribe form, including a status that has not loaded yet, because
    // the alternative is a screen with no way forward on it.
    // 'incomplete' belongs here, not in the card-update branch below. It means
    // Stripe accepted the subscription and never got paid, which for us is
    // almost always a 3D Secure challenge the cardholder abandoned. They do not
    // need to replace a working card; they need to buy again and finish the
    // popup this time. Stripe expires these on its own after 23 hours.
    const _status = (subscriptionData?.subscription_status || '').toLowerCase();
    const needsSubscription = ['', 'none', 'canceled', 'cancelled', 'deleted', 'incomplete', 'incomplete_expired'].includes(_status);

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
            const { subscriptionStatus, data, requiredAction } = await subscribeWithCard({
                stripe,
                card: cardElement,
                email,
                name: user?.displayName,
                plan: selectedPlan,
                // Which organization is being paid for. Without it the backend
                // guessed from the membership list and could bill a company the
                // customer merely administers rather than the one they are in.
                organizationId: activeOrganizationId,
                getToken: async () => (await user?.getIdToken()) || '',
            });

            posthog.capture('subscription_success', {
                userId: user?.uid,
                email: email,
                subscriptionStatus,
                plan: selectedPlan,
                requiredAction,
            });

            setSubscriptionData(data);
            setSubscriptionStatus(subscriptionStatus);
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
            // Fetched here rather than held in state. A SetupIntent secret is
            // per-attempt, so a value fetched when the screen mounted is the
            // wrong one by the second attempt.
            const token = await user?.getIdToken();
            const intentResponse = await fetch('/api/v1/subscriptions/setup-intent', {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
            });
            if (!intentResponse.ok) {
                throw new Error('Could not start the card update. Please try again.');
            }
            const { client_secret: setupSecret } = await intentResponse.json();

            const { setupIntent, error: stripeError } = await stripe.confirmCardSetup(setupSecret, {
                payment_method: {
                    card: cardElement,
                    billing_details: { email },
                },
            });

            if (stripeError) {
                throw new Error(stripeError.message || 'Failed to update payment method.');
            }

            // The backend reads this as `payment_method`. It was sent as
            // `payment_method_id`, which FastAPI rejected as a 422 before the
            // handler ran, so this request had never once succeeded.
            const response = await fetch('/api/v1/subscriptions/update-payment', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${token}`,
                },
                body: JSON.stringify({ payment_method: setupIntent?.payment_method }),
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
    // Named states only. This was written as "anything that is not one of five
    // known-good statuses", which quietly swept up every status nobody had
    // thought about — including 'incomplete', where the card is fine and the
    // customer merely closed their bank's popup. They were told their card had
    // expired, so they entered the same card and got the same message.
    const isCardUpdateRequired = ['past_due', 'unpaid'].includes(_status);

    return (
        <div className={`PaymentView ${darkMode ? 'dark-mode' : ''}`}>

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
            ) : needsSubscription ? (
                // No trial to offer. Access is bought here, or granted by being
                // invited into an organization that already pays.
                //
                // Reached by anyone without a live subscription, whichever way
                // they got there. A cancelled subscription used to have its own
                // branch offering a "Subscribe Again" button and nothing else:
                // no plan picker and, fatally, no CardElement. Pressing it made
                // handleSubscribe look for a card field that had never been
                // rendered, and the failure surfaced as "Payment system is
                // unavailable. Please refresh the page." Refreshing could not
                // help. Somebody whose plan lapsed was told the product was
                // broken and given no way to pay.
                //
                // Never had a subscription and had one that ended are the same
                // situation to a customer: no access, and a card to enter.
                <>
                    <PlanChoices plans={plans} selected={selectedPlan} onSelect={setSelectedPlan} />

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
                    {/* Which plan, not just that there is one. "Your
                        subscription is active" left an owner unable to tell
                        Starter from Business anywhere in the product, including
                        when deciding whether they had run out of seats. */}
                    <p>
                        {subscriptionData?.plan_name
                            ? <>
                                You are on <strong>{subscriptionData.plan_name}</strong>
                                {subscriptionData.seats_included
                                    ? `, ${subscriptionData.seats_included} seats included.`
                                    : '.'}
                              </>
                            : 'Your subscription is active.'}
                    </p>
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
