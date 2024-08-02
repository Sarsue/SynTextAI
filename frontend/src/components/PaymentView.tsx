// PaymentView.tsx
import React, { useState, useEffect } from 'react';
import { useStripe, useElements, CardElement } from '@stripe/react-stripe-js';
import type { Stripe } from '@stripe/stripe-js';
import './PaymentView.css'; // Import the CSS file
import { User } from 'firebase/auth';

interface PaymentViewProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null;
    subscriptionStatus: string | null;
    onSubscriptionChange: (newStatus: string) => void; // Callback function to notify parent component of subscription status change
    darkMode: boolean;
}

const CARD_ELEMENT_OPTIONS = {
    style: {
        base: {
            'color': '#32325d',
            'fontFamily': '"Helvetica Neue", Helvetica, sans-serif',
            'fontSmoothing': 'antialiased',
            'fontSize': '16px',
            '::placeholder': {
                color: '#aab7c4',
            },
        },
        invalid: {
            color: '#fa755a',
            iconColor: '#fa755a',
        },
    },
};

const PaymentView: React.FC<PaymentViewProps> = ({ stripePromise, user, subscriptionStatus, onSubscriptionChange, darkMode }) => {
    const stripe = useStripe();
    const elements = useElements();
    // const [email, setEmail] = useState('');
    const [email, setEmail] = useState((user ? user.email : '') || '');
    const [status, setStatus] = useState('');
    const [clientSecret, setClientSecret] = useState('');
    const [cancellationInitiated, setCancellationInitiated] = useState(false);
    const subscriptionPrice = 20;
    useEffect(() => {
        // Update local subscription status when prop changes
        setStatus(subscriptionStatus || '');
    }, [subscriptionStatus]);

    const handlePayment = async () => {
        if (!stripe || !elements) {
            console.error('Stripe.js has not loaded yet');
            return;
        }

        const cardElement = elements.getElement(CardElement);
        if (!cardElement) {
            console.error('Card element not found');
            return;
        }

        if (!user) {
            console.error('User not found');
            return;
        }
        if (status !== '') {
            const result = await stripe.confirmCardPayment(clientSecret, {
                payment_method: {
                    card: cardElement,
                    billing_details: {
                        email: email,
                    },
                },
            });
            if (result.error) {
                console.log(result.error.message);
                // Show error in payment form
            } else {
                console.log('Hell yea, you got that sub money!');
                setStatus('active'); // Update local subscription status
                onSubscriptionChange('active');
            }
        } else {
            // status is empty

            const result = await stripe.createPaymentMethod({
                type: 'card',
                card: cardElement,
                billing_details: {
                    email: email,
                },
            });

            if (result.error) {
                console.log(result.error.message);
                // Handle error in payment form
            } else {
                const payload = {
                    email: email,
                    payment_method: result.paymentMethod.id,
                };

                const token = await user.getIdToken();

                try {
                    const res = await fetch(`api/v1/subscriptions/sub`, {
                        method: 'POST',
                        headers: {
                            Authorization: `Bearer ${token}`,
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify(payload),
                    });

                    if (!res.ok) {
                        throw new Error(`HTTP error! Status: ${res.status}`);
                    }

                    const { client_secret, status } = await res.json();

                    if (status === 'requires_action') {
                        setStatus(status);
                        setClientSecret(client_secret);
                        const confirmResult = await stripe.confirmCardPayment(client_secret);

                        if (confirmResult.error) {
                            console.log(confirmResult.error.message);
                        } else {
                            console.log('Subscription successful!');
                            setStatus('active'); // Update local subscription status
                            onSubscriptionChange('active');
                        }
                    } else {
                        console.log('Subscription successful!');
                        setStatus('active'); // Update local subscription status
                        onSubscriptionChange('active');
                    }
                } catch (error) {
                    console.error('Fetch error:', error);
                    // Handle fetch error
                }
            }
        }
    };

    const handleCancellation = async () => {
        if (!user) {
            console.error('User not found');
            return;
        }
        console.log('Subscription cancellation initiated');
        setCancellationInitiated(true);

        try {
            const token = await user.getIdToken();

            const response = await fetch(`api/v1/subscriptions/cancel`, {
                method: 'post',
                headers: {
                    Authorization: `Bearer ${token}`,
                    'Content-Type': 'application/json',
                }
            });

            const cancelSubscriptionResponse = await response.json();
            console.log('Subscription cancellation response:', cancelSubscriptionResponse);
            setStatus(''); // Update local subscription status
            onSubscriptionChange('');
            // Display to the user that the subscription has been canceled.
        } catch (error) {
            console.error('Error during subscription cancellation:', error);
            // Handle the error and display appropriate messages to the user.
        }
    };

    return (
        <div className={`PaymentView ${darkMode ? 'dark-mode' : ''}`}>
            <h3>Payment Settings</h3>
            {/* Display subscription price */}
            <div>
                <p>Subscription Price: ${subscriptionPrice.toFixed(2)}</p>
            </div>
            <div>
                <label>Card Details</label>
                <CardElement options={CARD_ELEMENT_OPTIONS} />
                {/* Subscribe button is enabled when subscription status is not active */}
                {subscriptionStatus !== 'active' && (
                    <button onClick={handlePayment}>Subscribe</button>
                )}
            </div>
            <div>
                {/* Cancel Subscription button is enabled when subscription status is active */}
                {
                    subscriptionStatus === 'active' &&
                    (
                        <button onClick={handleCancellation}>Cancel Subscription</button>
                    )}
                {cancellationInitiated && (
                    <p>Cancellation initiated. You will be unsubscribed.</p>
                )}
            </div>
        </div>
    );
};

export default PaymentView;
