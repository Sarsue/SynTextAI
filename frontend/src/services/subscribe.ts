/**
 * Buying a subscription, in one place.
 *
 * WHY THIS IS NOT INSIDE PaymentView
 *
 * Two screens now take a card: settings, for somebody whose plan lapsed or who
 * never picked one, and signup, where naming a company and paying for it are a
 * single act. Both have to do the same awkward sequence, and the awkward part
 * is 3D Secure: a card that needs authentication does not decline, it leaves
 * the subscription `incomplete` until the cardholder passes a challenge, and
 * then the answer has to be read back from Stripe rather than from our own
 * database, which the confirming webhook has usually not reached yet.
 *
 * That is four steps with two failure modes, and a copy of it on each screen
 * would drift. The one on the screen nobody tests would drift first, and the
 * symptom would be a customer told their card was declined when it was not.
 */
import type { Stripe, StripeCardElement } from '@stripe/stripe-js';

export interface SubscribeOptions {
    stripe: Stripe;
    card: StripeCardElement;
    email: string;
    name?: string | null;
    plan: string;
    organizationId: number | null;
    /** Fetched per request: an ID token expires, and this flow spans a popup. */
    getToken: () => Promise<string>;
}

export interface SubscribeResult {
    subscriptionStatus: string;
    /** The subscribe response, for the card details the settings screen shows. */
    data: any;
    /** Whether the customer had to pass a bank challenge. Worth reporting. */
    requiredAction: boolean;
}

/** A message safe to show a customer, from whatever the server sent back. */
async function messageFrom(response: Response, fallback: string): Promise<string> {
    try {
        const body = await response.json();
        // FastAPI returns {"detail": ...}, never {"error": ...}. Reading .error
        // discarded every real message, including "your card was declined".
        const detail = body?.detail ?? body?.error;
        if (typeof detail === 'string') return detail;
        if (detail?.message) return detail.message;
    } catch {
        // Fall through to the generic message.
    }
    return fallback;
}

export async function subscribeWithCard(opts: SubscribeOptions): Promise<SubscribeResult> {
    const { stripe, card, email, name, plan, organizationId, getToken } = opts;

    const { paymentMethod, error: pmError } = await stripe.createPaymentMethod({
        type: 'card',
        card,
        billing_details: { email, name: name || 'Unknown User' },
    });
    if (pmError) throw new Error(pmError.message || 'Payment method creation failed.');

    const response = await fetch('/api/v1/subscriptions/subscribe', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${await getToken()}`,
        },
        body: JSON.stringify({
            payment_method: paymentMethod?.id,
            plan,
            organization_id: organizationId,
        }),
    });
    if (!response.ok) {
        throw new Error(await messageFrom(response, 'Failed to complete subscription.'));
    }

    const data = await response.json();
    let subscriptionStatus: string = data.subscription_status;

    // A card that needs 3D Secure does not decline. Stripe accepts the
    // subscription, leaves it 'incomplete', and waits for the cardholder to pass
    // their bank's challenge. Treating that as success lets somebody into an app
    // they have not paid for; treating it as failure tells them their card was
    // bad and invites them to re-enter the same good card. Finish it instead.
    if (data.requires_action && data.client_secret) {
        const { error: actionError } = await stripe.confirmCardPayment(data.client_secret);
        if (actionError) {
            throw new Error(
                actionError.message ||
                'Your bank did not confirm the payment. Please try again or use another card.'
            );
        }

        // Ask the server to re-read Stripe rather than reading our own database,
        // which the confirming webhook has probably not reached yet. See the
        // /confirm route for why this is not just a wait.
        const confirmResponse = await fetch('/api/v1/subscriptions/confirm', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${await getToken()}`,
            },
            body: JSON.stringify({ organization_id: organizationId }),
        });
        if (!confirmResponse.ok) {
            throw new Error(
                'Your payment went through, but we could not confirm it. Please refresh in a moment.'
            );
        }
        subscriptionStatus = (await confirmResponse.json()).subscription_status;
    }

    if (!['active', 'trialing'].includes((subscriptionStatus || '').toLowerCase())) {
        throw new Error('Your payment could not be completed. Please try another card.');
    }

    return {
        subscriptionStatus,
        data: { ...data, subscription_status: subscriptionStatus },
        requiredAction: Boolean(data.requires_action),
    };
}
