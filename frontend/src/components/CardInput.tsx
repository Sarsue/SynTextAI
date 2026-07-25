import React from 'react';
// stripe
import { CardElement } from '@stripe/react-stripe-js';

// Stripe's Elements render in a cross-origin iframe that can't read page CSS
// or resolve var() — the font stack is duplicated here literally to match
// --font in src/index.css as closely as Stripe's style API allows.
const CARD_ELEMENT_OPTIONS = {
    style: {
        base: {
            'color': '#32325d',
            'fontFamily': '"Source Sans 3", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
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

export default function CardInput() {
    return (
        <CardElement options={CARD_ELEMENT_OPTIONS} />
    );
}