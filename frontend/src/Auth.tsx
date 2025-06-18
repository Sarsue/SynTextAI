import React, { 
  useEffect, 
  useState, 
  useImperativeHandle, 
  forwardRef, 
  useCallback, 
  useRef 
} from 'react';
import {
  signInWithRedirect,
  signOut as firebaseSignOut,
  onAuthStateChanged,
  getRedirectResult,
  GoogleAuthProvider,
  setPersistence,
  browserLocalPersistence,
  signInWithPopup,
  User
} from 'firebase/auth';
import { useNavigate } from 'react-router-dom';
import { auth } from './firebase';
import { useUserContext } from './UserContext';
import { useAnalytics } from './hooks/useAnalytics';
import { getPosthog } from './utils/analyticsQueue';

// Extend the Window interface to include PostHog
declare global {
  interface Window {
    posthog?: {
      flush?: () => Promise<void> | void;
      // Add other PostHog methods if needed
      [key: string]: any;
    } | undefined;
  }
}

export interface AuthRef {
  logOut: () => Promise<void>;
}

interface AuthProps {}

const Auth = forwardRef<AuthRef, AuthProps>((props, ref) => {
  const navigate = useNavigate();
  const { setUser, user, subscriptionStatus, setSubscriptionStatus } = useUserContext();
  const [isLoading, setIsLoading] = useState(true);
  const isLoggingOut = useRef(false);
  const isDev = window.location.hostname === 'localhost';
  const { capture, identify, reset: resetAnalytics } = useAnalytics();

  // Safe wrapper for capture that handles the Promise return type
  const safeCapture = async (event: string, properties?: Record<string, any>) => {
    try {
      const result = await capture(event, properties);
      return result;
    } catch (error) {
      console.error(`Error capturing event ${event}:`, error);
      return null;
    }
  };

  // Handle navigation based on auth state
  useEffect(() => {
    if (user && subscriptionStatus !== null) {
      const targetPath = subscriptionStatus === 'active' || subscriptionStatus === 'trialing' 
        ? '/chat' 
        : '/settings';
      navigate(targetPath);
    }
  }, [user, subscriptionStatus, navigate]);

  // Initialize auth state and set up listener
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
              // Identify the user in analytics
              identify(authUser.uid, {
                email: authUser.email,
                name: authUser.displayName,
                uid: authUser.uid,
                provider: authUser.providerData?.[0]?.providerId
              });
              setUser(authUser);
              
              // Track login event
              await safeCapture('user_logged_in', {
                method: 'auto',
                timestamp: new Date().toISOString()
              });
            }
          } else if (user && !isLoggingOut.current) {
            // Only clear if we currently have a user and it's not a manual logout
            resetAnalytics();
            setUser(null);
            setSubscriptionStatus(null);
          }
        } catch (error) {
          console.error('Error in auth state change:', error);
          await safeCapture('auth_error', {
            error: error instanceof Error ? error.message : 'Unknown error',
            context: 'auth_state_change'
          });
        } finally {
          if (isLoading) {
            setIsLoading(false);
          }
        }
      });

      return () => {
        unsubscribe();
      };
    };

    initializeAuth();
  }, [setUser, setSubscriptionStatus, identify, capture, resetAnalytics, isLoading, user]);

  // Handle OAuth redirect result
  useEffect(() => {
    const handleRedirectResult = async () => {
      try {
        const result = await getRedirectResult(auth);
        if (result?.user) {
          setUser(result.user);
          const storedState = localStorage.getItem('authState');
          const redirectPath = storedState ? JSON.parse(storedState).redirectPath : '/chat';
          localStorage.removeItem('authState');
          
          const authIntent = localStorage.getItem('authIntent');
          const isNewUser = result.user.metadata.creationTime === result.user.metadata.lastSignInTime;
          
          try {
            if (isNewUser || authIntent === 'signup') {
              await safeCapture('user_signup', {
                isNewUser,
                fromSignupButton: authIntent === 'signup',
                method: 'google',
                timestamp: new Date().toISOString()
              });
              navigate('/welcome');
            } else {
              await safeCapture('user_signin', {
                fromSignupButton: authIntent === 'signup',
                method: 'google',
                timestamp: new Date().toISOString()
              });
              navigate(redirectPath);
            }
          } catch (error) {
            console.error('Error capturing auth event:', error);
            // Continue with navigation even if analytics fails
            navigate(isNewUser || authIntent === 'signup' ? '/welcome' : redirectPath);
          }
          
          localStorage.removeItem('authIntent');
        }
      } catch (error) {
        console.error('Redirect sign-in error:', error);
        safeCapture('auth_error', {
          error: error instanceof Error ? error.message : 'Unknown error',
          context: 'oauth_redirect'
        }).catch(console.error);
      }
    };

    handleRedirectResult();
  }, [setUser, navigate, safeCapture]);

  const signInWithGoogle = useCallback(async (intent: 'signin' | 'signup' = 'signin') => {
    if (user) return;

    const provider = new GoogleAuthProvider();
    try {
      // Store the intent before starting auth flow
      localStorage.setItem('authIntent', intent);
      localStorage.setItem('authState', JSON.stringify({ 
        redirectPath: window.location.pathname 
      }));

      // Sign out any existing session first
      await firebaseSignOut(auth);
      
      // Track sign-in attempt
      await safeCapture(`user_${intent}_attempt`, {
        method: 'google',
        timestamp: new Date().toISOString()
      });

      // Use popup in dev for easier debugging
      if (isDev) {
        await signInWithPopup(auth, provider);
      } else {
        await signInWithRedirect(auth, provider);
      }
    } catch (error) {
      console.error('Sign-in error:', error);
      await safeCapture('auth_error', {
        error: error instanceof Error ? error.message : 'Unknown error',
        context: `google_${intent}`,
        timestamp: new Date().toISOString()
      });
    }
  }, [user, isDev, capture]);

  const logOut = useCallback(async () => {
    // Return a promise that resolves when logout is complete
    return new Promise<void>(async (resolve, reject) => {
      // Prevent multiple logout attempts
      if (isLoggingOut.current) {
        console.log('[Logout] Logout already in progress');
        return reject(new Error('Logout already in progress'));
      }
      
      isLoggingOut.current = true;
      
      const logStep = (step: string) => {
        if (process.env.NODE_ENV === 'development') {
          console.log(`[Logout] ${step}`);
        }
      };

      try {
        logStep('Starting logout process');
        
        // 1. Reset user state immediately
        logStep('Resetting user state');
        const userEmail = user?.email || 'unknown';
        setUser(null);
        setSubscriptionStatus(null);
        
        // 2. Clear sensitive data from storage
        logStep('Clearing storage');
        const keysToRemove = [
          'authState', 
          'authIntent',
          'posthog_ph_last_event_time',
          'posthog_ph_last_event_ts',
          'posthog_ph_last_event_ts_/'
        ];
        
        keysToRemove.forEach(key => localStorage.removeItem(key));
        sessionStorage.clear();
        
        // Clear cookies
        document.cookie.split(';').forEach(cookie => {
          const [name] = cookie.split('=');
          document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
        });
        
        // 3. Sign out from Firebase
        logStep('Signing out from Firebase');
        try {
          await firebaseSignOut(auth);
          logStep('Firebase sign out successful');
        } catch (error) {
          console.error('[Logout] Error signing out from Firebase:', error);
          // Continue with logout even if Firebase sign out fails
        }
        
        // 4. Queue up analytics operations to run after logout
        const trackAnalytics = async () => {
          try {
            await safeCapture('user_logout_attempt', {
              timestamp: new Date().toISOString(),
              source: 'auth_component',
              user_email: userEmail
            });
            
            // Reset analytics after capturing logout event
            resetAnalytics();
            
            // If PostHog is available and has flush method, flush events
            const posthog = getPosthog();
            if (posthog?.flush) {
              try {
                const flushResult = posthog.flush();
                if (flushResult && typeof flushResult.then === 'function') {
                  await flushResult.catch(() => {});
                }
              } catch (e) {
                console.error('[PostHog] Error flushing events:', e);
              }
            }
          } catch (e) {
            console.error('[Logout] Error in analytics cleanup:', e);
          }
        };
        
        // Don't await - let it run in background
        trackAnalytics().catch(() => {});
        
        // 5. Mark logout as complete before redirect
        logStep('Logout process completed');
        isLoggingOut.current = false;
        
        // 6. Resolve the promise
        resolve();
        
        // 7. Redirect to login
        logStep('Redirecting to login');
        window.location.href = '/login';
        
      } catch (error) {
        console.error('[Logout] Error during logout:', error);
        isLoggingOut.current = false;
        reject(error);
        
        // Ensure we still redirect even if something fails
        window.location.href = '/';
      }
    });
  }, [capture, resetAnalytics, setUser, setSubscriptionStatus]);
  
  // Expose logOut via ref
  useImperativeHandle(ref, () => ({
    logOut
  }), [logOut]);

  if (isLoading) {
    return <div>Loading...</div>;
  }

  return (
    <div className="auth-container">
      {!user ? (
        <div className="auth-buttons" style={{ display: 'flex', gap: '15px' }}>
          <button 
            className="signin-link"
            onClick={() => signInWithGoogle('signin')}
            style={{
              background: 'transparent',
              border: '1px solid var(--accent-color)',
              color: 'var(--accent-color)',
              padding: '8px 15px',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: 500,
              transition: 'all 0.2s ease'
            }}
          >
            Sign in with Google
          </button>
          <button 
            className="signup-button"
            onClick={() => signInWithGoogle('signup')}
            style={{
              background: 'var(--accent-color)',
              color: 'white',
              border: 'none',
              padding: '8px 15px',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: 500,
              transition: 'all 0.2s ease'
            }}
          >
            Sign up with Google
          </button>
        </div>
      ) : (
        <button 
          className="signup-button"
          onClick={logOut}
          disabled={isLoggingOut.current}
          style={{
            background: 'var(--accent-color)',
            color: 'white',
            border: 'none',
            padding: '8px 15px',
            borderRadius: '4px',
            cursor: 'pointer',
            fontWeight: 500,
            opacity: isLoggingOut.current ? 0.7 : 1
          }}
        >
          {isLoggingOut.current ? 'Signing out...' : 'Sign Out'}
        </button>
      )}
    </div>
  );
});

export default Auth;
