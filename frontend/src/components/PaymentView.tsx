import React, { useState, useEffect } from 'react';
import { useStripe } from '@stripe/react-stripe-js';
import type { Stripe } from '@stripe/stripe-js';
import './PaymentView.css';
import { User } from 'firebase/auth';
import BuyButtonComponent from './BuyButtonComponent';

interface PaymentViewProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null;
    subscriptionStatus: string | null;
    onSubscriptionChange: (newStatus: string) => void;
    darkMode: boolean;
}



const PaymentView: React.FC<PaymentViewProps> = ({ stripePromise, user, subscriptionStatus, onSubscriptionChange, darkMode }) => {
    const stripe = useStripe();
    const [email, setEmail] = useState(user?.email || '');
    const [clientSecret, setClientSecret] = useState('');
    const [cancellationInitiated, setCancellationInitiated] = useState(false);
    const subscriptionPrice = 15;



    return (
        <div className={`PaymentView ${darkMode ? 'dark-mode' : ''}`}>
            <h3>Payment </h3>
            <div>
                <p>Subscription Price: ${subscriptionPrice.toFixed(2)}</p>
            </div>
            {subscriptionStatus !== 'active' ? (
                <>
                    <BuyButtonComponent
                        buyButtonId="buy_btn_1PmFYiHuDDTkwuzjuYQGB6IQ"
                        publishableKey="pk_live_51OXYPHHuDDTkwuzjvUVcNwur0xLQx7UWYfMN6d8hjbHUMhYlu7IJx0qEGyvZQhbIGSRKDxhCuXk6e1rQgnSh5XXu004fNj9Pwj"
                    />
                </>
            ) : (
                <button>Manage Subscription</button>
            )}
        </div>
    );
};

export default PaymentView;
