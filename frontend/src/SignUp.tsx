import React, { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAuth, GoogleAuthProvider, signInWithPopup } from 'firebase/auth';
import { useUserContext } from './UserContext';
import { useToast } from './contexts/ToastContext';
import { Button } from '@/components/ui/button';

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
const SignUp: React.FC = () => {
    const navigate = useNavigate();
    const { user, darkMode, setActiveOrganization, fetchSubscriptionStatus } = useUserContext();
    const { addToast } = useToast();
    const [isWorking, setIsWorking] = useState(false);

    const startOrganization = useCallback(async () => {
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
            body: JSON.stringify({ firebase_uid: current.uid, email: current.email }),
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.detail || 'Could not create your company account.');
        }
        return await response.json();
    }, []);

    const finish = useCallback(async (organizationId: number | null) => {
        if (organizationId) {
            await setActiveOrganization(organizationId);
        }
        await fetchSubscriptionStatus();
        // Straight to billing: an organization with no subscription is entitled
        // to nothing, so landing in the app would only show a locked one.
        navigate('/settings', { replace: true });
    }, [navigate, setActiveOrganization, fetchSubscriptionStatus]);

    // Already signed in, as an invited member or otherwise. No second Google
    // round trip: the account in hand is the one starting the company.
    const signUpWithCurrentAccount = useCallback(async () => {
        setIsWorking(true);
        try {
            const data = await startOrganization();
            await finish(data?.organization_id ?? null);
        } catch (e) {
            addToast(e instanceof Error ? e.message : 'Something went wrong', 'error');
        } finally {
            setIsWorking(false);
        }
    }, [startOrganization, finish, addToast]);

    const signUpWithGoogle = useCallback(async () => {
        setIsWorking(true);
        try {
            // Read by the auth listener, which registers the account before
            // this component gets another turn.
            sessionStorage.setItem('auth_intent', 'signup');
            await signInWithPopup(getAuth(), new GoogleAuthProvider());
            const data = await startOrganization();
            await finish(data?.organization_id ?? null);
        } catch (e) {
            addToast(e instanceof Error ? e.message : 'Could not sign up', 'error');
        } finally {
            setIsWorking(false);
        }
    }, [startOrganization, finish, addToast]);

    return (
        <div className={`auth-page ${darkMode ? 'dark-mode' : ''}`}>
            <div className="auth-card">
                <h1 className="auth-title">Syntext</h1>
                <p className="auth-sub">
                    Start a company account. You will pick a plan and add a card.
                </p>

                {user ? (
                    <>
                        <p className="auth-hint">
                            Signed in as {user.email}. This creates a company account you own,
                            separate from any you have been invited to.
                        </p>
                        <Button
                            className="w-full"
                            onClick={signUpWithCurrentAccount}
                            disabled={isWorking}
                        >
                            {isWorking ? 'Setting up...' : 'Create my company account'}
                        </Button>
                        <p className="auth-hint auth-hint-quiet">
                            <button
                                type="button"
                                className="auth-link"
                                onClick={() => navigate('/chat')}
                            >
                                Back to the app
                            </button>
                        </p>
                    </>
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
