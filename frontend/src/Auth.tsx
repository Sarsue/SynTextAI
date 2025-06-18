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

            const unsubscribe = onAuthStateChanged(auth, async (authUser) => {
                try {
                    if (authUser) {
                        // Only update if we don't already have a user or if the user ID changed
                        if (!user || user.uid !== authUser.uid) {
                            // Identify the user in PostHog
                            posthog.identify(authUser.uid, {
                                email: authUser.email,
                                name: authUser.displayName,
                                uid: authUser.uid,
                                provider: authUser.providerData?.[0]?.providerId
                            });
                            setUser(authUser);
                        }
                    } else {
                        // Only clear if we currently have a user
                        if (user) {
                            // Reset PostHog identity
                            posthog.reset();
                            setUser(null);
                            setSubscriptionStatus(null);
                        }
                    }
                } catch (error) {
                    console.error('Error in auth state change:', error);
                } finally {
                    if (isLoading) {
                        setIsLoading(false);
                    }
                }
            });

            return () => {
                // Clean up the auth state listener
                unsubscribe();
                
                // Reset PostHog on unmount if user is logged out
                if (!user) {
                    posthog.reset();
                }
            };
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
        console.log('=== Starting logout process ===');
        
        try {
            console.log('1. Resetting PostHog');
            posthog.reset();
            
            // Log current auth state before clearing
            console.log('2. Current auth state before signOut:', {
                currentUser: auth.currentUser?.uid,
                isLoggedIn: !!auth.currentUser,
                hasSession: document.cookie.includes('session=')
            });
            
            console.log('3. Clearing local storage items');
            const keysToRemove = [
                'authState', 
                'authIntent',
                'posthog_ph_last_event_time',
                'posthog_ph_last_event_ts',
                'posthog_ph_last_event_ts_/'
            ];
            
            keysToRemove.forEach(key => {
                const existed = localStorage.getItem(key) !== null;
                localStorage.removeItem(key);
                console.log(`   - Cleared ${key}: ${existed ? 'existed' : 'did not exist'}`);
            });
            
            console.log('4. Clearing session storage');
            const sessionKeys = Object.keys(sessionStorage);
            sessionStorage.clear();
            console.log(`   - Cleared ${sessionKeys.length} session items`);
            
            console.log('5. Clearing cookies');
            const cookies = document.cookie.split(';');
            cookies.forEach(cookie => {
                const [name] = cookie.split('=');
                document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
                console.log(`   - Cleared cookie: ${name}`);
            });
            
            console.log('6. Signing out from Firebase');
            await signOut(auth);
            console.log('   - Firebase signOut completed');
            
            console.log('7. Resetting user state');
            setUser(null);
            setSubscriptionStatus(null);
            
            console.log('8. Final PostHog reset');
            posthog.reset(true);
            
            console.log('9. Reloading page');
            // Small delay to ensure all state is cleared before reload
            setTimeout(() => {
                window.location.href = '/';
                window.location.reload();
            }, 100);
            
        } catch (error) {
            console.error('!!! Logout error !!!', {
                error: error instanceof Error ? error.message : 'Unknown error',
                stack: error instanceof Error ? error.stack : undefined,
                time: new Date().toISOString()
            });
            // Even if there's an error, try to force a reload
            window.location.href = '/';
        } finally {
            console.log('=== Logout process completed ===');
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
