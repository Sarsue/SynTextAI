import * as React from 'react';

// Define the type for the component props if needed
interface BuyButtonComponentProps { }

const BuyButtonComponent: React.FC<BuyButtonComponentProps> = () => {
    React.useEffect(() => {
        // Ensure the Stripe buy button script is loaded
        const script = document.createElement('script');
        script.src = 'https://buy.stripe.com/stripe-buy-button.js';
        script.async = true;
        script.onload = () => {
            console.log('Stripe buy button script loaded successfully.');
        };
        script.onerror = () => {
            console.error('Error loading Stripe buy button script.');
        };
        document.body.appendChild(script);

        return () => {
            document.body.removeChild(script);
        };
    }, []);

    const handleError = (event: React.SyntheticEvent<HTMLElement, Event>) => {
        console.error('Stripe buy button error:', event);
    };

    return (
        <stripe-buy-button
            buy-button-id="buy_btn_1PegIsHuDDTkwuzj6Cx6opwZ" // Replace with your actual BUY_BUTTON_ID
            publishable-key="pk_test_51OXYPHHuDDTkwuzjkvYwr2LSyu3Jh2gE9M6BZeIc7VPgoIJHhk36wd1qAwt07NymVuMf4tpd17ClOWFSXah5sX5600k65gqzcD"
            onError={handleError}
        >
        </stripe-buy-button>
    );
}

export default BuyButtonComponent;

// Declare the custom element type in the global JSX namespace
declare global {
    namespace JSX {
        interface IntrinsicElements {
            'stripe-buy-button': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
                'buy-button-id': string;
                'publishable-key': string;
                onError?: (event: React.SyntheticEvent<HTMLElement, Event>) => void;
            };
        }
    }
}
