import React, { useState, useEffect } from 'react';
import { useStripe, useElements, Elements, CardElement } from '@stripe/react-stripe-js';
import type { Stripe } from '@stripe/stripe-js';
import './PaymentView.css';
import { User } from 'firebase/auth';

interface PaymentViewProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null;
    subscriptionStatus: string | null;
    onSubscriptionChange: (newStatus: string) => void;
    darkMode: boolean;
}

const PaymentView: React.FC<PaymentViewProps> = ({ stripePromise, user, subscriptionStatus, onSubscriptionChange, darkMode }) => {
    const stripe = useStripe();
    const elements = useElements();
    const [email, setEmail] = useState(user?.email || '');
    const [clientSecret, setClientSecret] = useState('');
    const [cancellationInitiated, setCancellationInitiated] = useState(false);
    const [subscriptionData, setSubscriptionData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const subscriptionPrice = 15; // Define the subscription price here

    useEffect(() => {
        if (user) {
            fetchSubscriptionStatus();
        }
    }, [user]);

    const fetchSubscriptionStatus = async () => {
        setLoading(true);
        try {
            const token = await user?.getIdToken();
            const res = await fetch('/api/v1/subscriptions/status', {
                method: 'GET',
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            });
            const data = await res.json();
            setSubscriptionData(data);
            onSubscriptionChange(data.subscription_status);
        } catch (error) {
            setError('Failed to fetch subscription status');
        } finally {
            setLoading(false);
        }
    };

    const handleCancelSubscription = async () => {
        setLoading(true);
        try {
            const token = await user?.getIdToken();
            const res = await fetch('/api/v1/subscriptions/cancel', {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            });
            if (res.ok) {
                setSubscriptionData({ ...subscriptionData, subscription_status: 'canceled' });
                onSubscriptionChange('canceled');
            } else {
                const { error } = await res.json();
                setError(error);
            }
        } catch (error) {
            setError('Failed to cancel subscription');
        } finally {
            setLoading(false);
        }
    };

    const handleSubmit = async (event: React.FormEvent) => {
        event.preventDefault();
        if (!stripe || !elements) {
            return;
        }

        setLoading(true);
        setError(null);

        const { token, error: stripeError } = await stripe.createToken(elements.getElement(CardElement)!);

        if (stripeError) {
            setError(stripeError.message || 'An unknown error occurred');
            setLoading(false);
            return;
        }

        try {
            const response = await fetch('/api/v1/subscriptions/sub', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${await user?.getIdToken()}`,
                },
                body: JSON.stringify({ payment_method: token?.id }),
            });

            if (response.ok) {
                setSubscriptionData({ ...subscriptionData, subscription_status: 'active' });
                onSubscriptionChange('active');
            } else {
                const { error } = await response.json();
                setError(error || 'An unknown error occurred');
            }
        } catch (error) {
            setError('Failed to subscribe');
        } finally {
            setLoading(false);
        }
    };

    const handleUpdatePayment = async (event: React.FormEvent) => {
        event.preventDefault();
        if (!stripe || !elements) {
            return;
        }

        setLoading(true);
        setError(null);

        const { token, error: stripeError } = await stripe.createToken(elements.getElement(CardElement)!);

        if (stripeError) {
            setError(stripeError.message || 'An unknown error occurred');
            setLoading(false);
            return;
        }

        try {
            const response = await fetch('/api/v1/subscriptions/update-payment', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${await user?.getIdToken()}`,
                },
                body: JSON.stringify({ payment_method: token?.id }),
            });

            if (response.ok) {
                setSubscriptionData({ ...subscriptionData, subscription_status: 'active' });
                onSubscriptionChange('active');
            } else {
                const { error } = await response.json();
                setError(error || 'An unknown error occurred');
            }
        } catch (error) {
            setError('Failed to update payment method');
        } finally {
            setLoading(false);
        }
    };

    if (loading) return <div>Loading...</div>;

    const showUpdatePaymentForm = subscriptionData?.subscription_status === 'card_expired';

    return (
        <div className={`PaymentView ${darkMode ? 'dark-mode' : ''}`}>
            <h3>Payment</h3>
            <div>
                <p>Subscription Price: ${subscriptionPrice.toFixed(2)}</p>
            </div>
            {showUpdatePaymentForm ? (
                <Elements stripe={stripePromise}>
                    <form onSubmit={handleUpdatePayment}>
                        <CardElement />
                        <button type="submit" disabled={loading}>
                            {loading ? 'Processing...' : 'Update Payment'}
                        </button>
                        {error && <p className="error">{error}</p>}
                    </form>
                </Elements>
            ) : (
                <>
                    {subscriptionData?.subscription_status === 'active' ? (
                        <>
                            <p>Your subscription is active.</p>
                            <button onClick={handleCancelSubscription}>Cancel Subscription</button>
                        </>
                    ) : (
                        <Elements stripe={stripePromise}>
                            <form onSubmit={handleSubmit}>
                                <CardElement />
                                <button type="submit" disabled={loading}>
                                    {loading ? 'Processing...' : 'Subscribe'}
                                </button>
                                {error && <p className="error">{error}</p>}
                            </form>
                        </Elements>
                    )}
                </>
            )}
            {error && <p className="error">{error}</p>}
        </div>
    );
};

export default PaymentView;
