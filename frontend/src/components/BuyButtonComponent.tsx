import * as React from 'react';

interface BuyButtonComponentProps {
    buyButtonId: string;
    publishableKey: string;
}

const BuyButtonComponent: React.FC<BuyButtonComponentProps> = ({ buyButtonId, publishableKey }) => {
    React.useEffect(() => {
        const script = document.createElement('script');
        script.src = 'https://billing.stripe.com/p/login/3cs6r94FKeJw9aw7ss'//'https://buy.stripe.com/28o5nk6Jp1ZT3T25kl';
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
            buy-button-id={buyButtonId}
            publishable-key={publishableKey}
            onError={handleError}
        />
    );
}

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

export default BuyButtonComponent;
