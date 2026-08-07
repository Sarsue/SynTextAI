import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAuth, GoogleAuthProvider, signInWithPopup } from 'firebase/auth';
import { useStripe, useElements, CardElement } from '@stripe/react-stripe-js';
import { useUserContext } from './UserContext';
import { useToast } from './contexts/ToastContext';
import { Button } from '@/components/ui/button';
import { PlanChoices, usePlans } from './components/PlanPicker';
import { subscribeWithCard } from './services/subscribe';
import posthog from './services/analytics';
import './components/PaymentView.css';

/**
 * Starting a company account.
 *
 * Deliberately its own route rather than a mode on the sign-in page. The two
 * are different acts: signing in enters a company you already belong to,
 * signing up creates one you own and pay for. Sharing a screen meant sharing a
 * signed-in state too, and that state was "you are already in, here is a sign
 * out button" — so somebody who had been invited into a company could not
 * reach sign up at all without first signing out, which is not something a
 * person thinks to do in order to buy something.
 *
 * Both cases land here:
 *
 *   signed out  authenticate with Google, then start the organization
 *   signed in   start the organization on the account already in hand
 */
/** The company this account owns, once we have asked. */
interface OwnedCompany {
    organization_id: number;
    name: string;
}

const SignUp: React.FC = () => {
    const navigate = useNavigate();
    const { user, darkMode, setActiveOrganization, fetchSubscriptionStatus } = useUserContext();
    const { addToast } = useToast();
    const stripe = useStripe();
    const elements = useElements();
    const [isWorking, setIsWorking] = useState(false);
    const [companyName, setCompanyName] = useState('');
    const [selectedPlan, setSelectedPlan] = useState<string>('starter');
    const plans = usePlans(setSelectedPlan);

    // The company, once it exists. A card can be declined after the
    // organization has already been created, and an account owns exactly one
    // company: posting the name again on the retry returns the existing one
    // unchanged, so an edited name would be quietly ignored. Remember the id,
    // skip straight to payment on the retry, and stop pretending the field is
    // still live.
    const [createdOrgId, setCreatedOrgId] = useState<number | null>(null);

    // Does this account already own a company?
    //
    // undefined while we have not asked, null once we know it does not. A
    // signed-in visitor was offered "Create my company account" regardless, and
    // pressing it returned the company they already had — the backend is
    // idempotent and the database refuses a second one, so nothing broke, but
    // the button described an act it was never going to perform. Somebody who
    // reached this screen by accident had no way to tell that from a screen
    // that would take their money.
    const [owned, setOwned] = useState<OwnedCompany | null | undefined>(undefined);

    useEffect(() => {
        if (!user) { setOwned(null); return; }
        let cancelled = false;
        (async () => {
            try {
                const idToken = await user.getIdToken();
                const res = await fetch('/api/v1/organizations', {
                    headers: { Authorization: `Bearer ${idToken}` },
                });
                if (!res.ok) throw new Error('could not load organizations');
                const data = await res.json();
                if (cancelled) return;
                const mine = (data.items || []).find((o: any) => o.role === 'owner');
                setOwned(mine ? { organization_id: mine.organization_id, name: mine.name } : null);
            } catch {
                // Offer to create rather than blocking on a failed lookup. The
                // backend returns the existing company if there is one, so the
                // worst case is a button that turns out to be a no-op.
                if (!cancelled) setOwned(null);
            }
        })();
        return () => { cancelled = true; };
    }, [user]);

    const startOrganization = useCallback(async (name: string) => {
        const auth = getAuth();
        const current = auth.currentUser;
        if (!current) return null;
        const idToken = await current.getIdToken();
        const response = await fetch('/api/v1/users?intent=signup', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${idToken}`,
            },
            // The company's name, chosen rather than derived. Everything else
            // this endpoint needs is in the token; the body used to carry the
            // uid and email, which it read from the token anyway and ignored.
            body: JSON.stringify({ company_name: name }),
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.detail || 'Could not create your company account.');
        }
        return await response.json();
    }, []);

    /** Name the company, pay for it, go in. One submit. */
    const createAndSubscribe = useCallback(async (e: React.FormEvent) => {
        e.preventDefault();
        const card = elements?.getElement(CardElement);
        if (!stripe || !card) {
            addToast('Payment system is unavailable. Please refresh the page.', 'error');
            return;
        }
        const name = companyName.trim();
        if (!name) {
            addToast('What is your company called?', 'error');
            return;
        }

        setIsWorking(true);
        try {
            // Create first, pay second, and both before leaving the screen.
            // A subscription belongs to an organization, so there is nothing to
            // charge until one exists. This used to be two screens with a
            // redirect between them, and the company arrived already named
            // after the email prefix.
            let organizationId = createdOrgId;
            if (!organizationId) {
                const created = await startOrganization(name);
                organizationId = created?.organization_id ?? null;
                if (!organizationId) throw new Error('Could not create your company account.');
                setCreatedOrgId(organizationId);
            }
            await setActiveOrganization(organizationId);

            const { subscriptionStatus, requiredAction } = await subscribeWithCard({
                stripe,
                card,
                email: user?.email || '',
                name: user?.displayName,
                plan: selectedPlan,
                organizationId,
                getToken: async () => (await getAuth().currentUser?.getIdToken()) || '',
            });

            posthog.capture('signup_completed', {
                userId: user?.uid,
                plan: selectedPlan,
                subscriptionStatus,
                requiredAction,
            });

            // Entitlement lives on the organization and /chat is gated on it,
            // so resolve it before navigating or the guard sends them back.
            await fetchSubscriptionStatus();
            navigate('/chat', { replace: true });
        } catch (err) {
            // Deliberately stays on this screen with the company created. The
            // organization is theirs either way, and settings can take the card
            // later; throwing it away because a card failed would mean typing
            // the name again.
            addToast(err instanceof Error ? err.message : 'Something went wrong', 'error');
        } finally {
            setIsWorking(false);
        }
    }, [
        stripe, elements, companyName, selectedPlan, user, createdOrgId,
        startOrganization, setActiveOrganization, fetchSubscriptionStatus, navigate, addToast,
    ]);

    const signUpWithGoogle = useCallback(async () => {
        setIsWorking(true);
        try {
            // Note what is NOT set here: auth_intent. It used to be 'signup',
            // which made the sign-in listener create the organization the moment
            // Firebase reported the account, named after the email prefix,
            // before anybody had been asked what the company is called. The
            // listener now registers the user and stops, and the form below
            // creates the organization with the name they chose.
            await signInWithPopup(getAuth(), new GoogleAuthProvider());
        } catch (e) {
            addToast(e instanceof Error ? e.message : 'Could not sign up', 'error');
        } finally {
            setIsWorking(false);
        }
    }, [addToast]);

    return (
        <div className={`auth-page ${darkMode ? 'dark-mode' : ''}`}>
            <div className={`auth-card ${user && !owned ? 'auth-card-wide' : ''}`}>
                <h1 className="auth-title">Syntext</h1>
                <p className="auth-sub">
                    {owned
                        ? 'You already have a company account.'
                        : 'Name your company, pick a plan, and you are in.'}
                </p>

                {user && owned === undefined ? (
                    <p className="auth-hint">Checking your account...</p>
                ) : user && owned ? (
                    // Already has one. You sign up once; everything after that
                    // is an invitation. Offering to create a second company
                    // here would describe an act the database refuses.
                    <>
                        <p className="auth-hint">
                            Signed in as {user.email}. You already own{' '}
                            <strong>{owned.name}</strong>, and an account owns one company.
                            You can be invited into as many others as you like.
                        </p>
                        <Button
                            className="w-full"
                            disabled={isWorking}
                            onClick={async () => {
                                setIsWorking(true);
                                await setActiveOrganization(owned.organization_id);
                                navigate('/chat', { replace: true });
                            }}
                        >
                            {isWorking ? 'Opening...' : `Go to ${owned.name}`}
                        </Button>
                        <p className="auth-hint auth-hint-quiet">
                            <button
                                type="button"
                                className="auth-link"
                                onClick={() => navigate('/select-organization')}
                            >
                                Switch to another organization
                            </button>
                        </p>
                    </>
                ) : user ? (
                    // Naming the company and paying for it, on one screen.
                    // These were two steps with a redirect between them, and the
                    // company was named for the customer before they were asked.
                    <form onSubmit={createAndSubscribe}>
                        <p className="auth-hint">
                            Signed in as {user.email}. This creates a company account you own,
                            separate from any you have been invited to.
                        </p>

                        <label className="auth-field">
                            <span className="auth-field-label">Company name</span>
                            <input
                                type="text"
                                className="auth-input"
                                value={companyName}
                                onChange={(e) => setCompanyName(e.target.value)}
                                placeholder="Northgate Dental"
                                maxLength={100}
                                autoFocus
                                required
                                disabled={createdOrgId !== null}
                            />
                        </label>
                        <p className="auth-hint auth-hint-quiet">
                            {createdOrgId !== null
                                ? 'Your company is created. Add a card to finish, or rename it later in settings.'
                                : 'This is what your team sees, and what your invites say. You can change it later.'}
                        </p>

                        <PlanChoices plans={plans} selected={selectedPlan} onSelect={setSelectedPlan} />

                        <CardElement
                            options={{
                                style: {
                                    base: {
                                        color: darkMode ? '#ffffff' : '#000000',
                                        backgroundColor: darkMode ? '#333' : '#ffffff',
                                        '::placeholder': { color: darkMode ? '#bbbbbb' : '#888888' },
                                    },
                                },
                            }}
                        />

                        <Button className="w-full" type="submit" disabled={isWorking || !plans.length}>
                            {isWorking ? 'Setting up...' : 'Create my company account'}
                        </Button>
                        <p className="billing-note">
                            Billed monthly. Seats beyond the included allowance are added to your
                            next invoice as you invite people, and removed as soon as you remove
                            them.
                        </p>
                        <p className="auth-hint auth-hint-quiet">
                            <button
                                type="button"
                                className="auth-link"
                                onClick={() => navigate('/chat')}
                            >
                                Back to the app
                            </button>
                        </p>
                    </form>
                ) : (
                    <>
                        <Button
                            variant="outline"
                            className="auth-google-btn w-full"
                            onClick={signUpWithGoogle}
                            disabled={isWorking}
                        >
                            {isWorking ? 'Setting up...' : (
                                <>
                                    <svg width="16" height="16" viewBox="0 0 18 18" fill="none">
                                        <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" fill="#4285F4" />
                                        <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332C2.438 15.983 5.482 18 9 18z" fill="#34A853" />
                                        <path d="M3.964 10.707c-.18-.54-.282-1.117-.282-1.707 0-.593.102-1.17.282-1.709V4.958H.957C.347 6.173 0 7.548 0 9c0 1.452.348 2.827.957 4.042l3.007-2.335z" fill="#FBBC05" />
                                        <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.462.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335" />
                                    </svg>
                                    Sign up with Google
                                </>
                            )}
                        </Button>
                        <p className="auth-hint">
                            Already have an account?{' '}
                            <button
                                type="button"
                                className="auth-link"
                                onClick={() => navigate('/login')}
                            >
                                Sign in
                            </button>
                        </p>
                    </>
                )}

                <p className="auth-hint auth-hint-quiet">
                    Joining a team? Use the invite link your colleague sent you. You do not
                    need a company account of your own.
                </p>
            </div>
        </div>
    );
};

export default SignUp;
