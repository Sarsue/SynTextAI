import React, { FC, useEffect, useState } from 'react';
import {
    signInWithRedirect,
    signOut,
    onAuthStateChanged,
    getRedirectResult,
    GoogleAuthProvider,
    setPersistence,
    browserLocalPersistence,
    signInWithPopup
} from 'firebase/auth';
import { useNavigate } from 'react-router-dom';
import { auth } from './firebase';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';

const Auth: FC = () => {
    const navigate = useNavigate();
    const { setUser, user, subscriptionStatus, setSubscriptionStatus } = useUserContext();
    const [isLoading, setIsLoading] = useState(true);
    const isDev = window.location.hostname === 'localhost'; // Check if in dev mode
    const posthog = usePostHog();

    useEffect(() => {
        if (user && subscriptionStatus !== null) {
            navigate(subscriptionStatus === 'active' || subscriptionStatus === 'trialing' ? '/chat' : '/settings');
        }
    }, [user, subscriptionStatus, navigate]);

    useEffect(() => {
        const initializeAuth = async () => {
            try {
                await setPersistence(auth, browserLocalPersistence);
            } catch (error) {
                console.error('Failed to set persistence:', error);
            }

            const unsubscribe = onAuthStateChanged(auth, (authUser) => {
                if (authUser) {
                    setUser(authUser);
                } else {
                    setUser(null);
                    setSubscriptionStatus(null);
                }
                setIsLoading(false);
            });

            return unsubscribe;
        };

        initializeAuth();
    }, [setUser, setSubscriptionStatus]);

    useEffect(() => {
        getRedirectResult(auth)
            .then((result) => {
                if (result?.user) {
                    setUser(result.user);
                    const storedState = localStorage.getItem('authState');
                    const redirectPath = storedState ? JSON.parse(storedState).redirectPath : '/chat';
                    localStorage.removeItem('authState');
                    
                    // Check if this is a sign-up or sign-in intent
                    const authIntent = localStorage.getItem('authIntent');
                    
                    // Check if this is a new user (metadata.creationTime === metadata.lastSignInTime)
                    const isNewUser = result.user.metadata.creationTime === result.user.metadata.lastSignInTime;
                    
                    if (isNewUser || authIntent === 'signup') {
                        // This is likely a new user or explicitly signed up
                        posthog.capture('user_signup', {
                            isNewUser: isNewUser,
                            fromSignupButton: authIntent === 'signup'
                        });
                        
                        // Redirect to onboarding or welcome page
                        navigate('/welcome');
                    } else {
                        // This is a returning user
                        posthog.capture('user_signin', {
                            fromSignupButton: authIntent === 'signup'
                        });
                        
                        navigate(redirectPath);
                    }
                    
                    localStorage.removeItem('authIntent');
                }
            })
            .catch((error) => {
                console.error('Redirect sign-in error:', error);
            });
    }, [setUser, navigate, posthog]);

    const signInWithGoogle = async () => {
        if (user) return; // Avoid double redirects

        const provider = new GoogleAuthProvider();
        try {
            await signOut(auth);
            localStorage.setItem('authState', JSON.stringify({ redirectPath: window.location.pathname }));

            if (isDev) {
                // Use popup if in development mode
                await signInWithPopup(auth, provider);
            } else {
                // Use redirect for production
                await signInWithRedirect(auth, provider);
            }
        } catch (error) {
            console.error('Sign-in error:', error);
        }
    };

    const logOut = async () => {
        try {
            await signOut(auth);
            setUser(null);
            setSubscriptionStatus(null);
        } catch (error) {
            console.error('Logout error:', error);
        }
    };

    if (isLoading) {
        return <div>Loading...</div>;
    }

    return (
        <div>
            {!user ? (
                <button onClick={signInWithGoogle}>Sign in with Google</button>
            ) : (
                <button onClick={logOut}>Log Out</button>
            )}
        </div>
    );
};

export default Auth;
