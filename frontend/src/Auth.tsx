import React, { 
  useEffect, 
  useState, 
  useImperativeHandle, 
  forwardRef, 
  useCallback, 
  useRef 
} from 'react';
import {
  signOut as firebaseSignOut,
  GoogleAuthProvider,
  signInWithPopup,
  User
} from 'firebase/auth';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useToast } from './contexts/ToastContext';
import { auth } from './firebase';
import { useUserContext } from './UserContext';
import { useAnalytics } from './hooks/useAnalytics';
import { getPosthog } from './utils/analyticsQueue';
import { Button } from '@/components/ui/button';

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
  const { user, subscriptionStatus, authLoading } = useUserContext();
  const [isSigningIn, setIsSigningIn] = useState(false);
  const { addToast } = useToast();
  // Sign up and sign in are the same Google call underneath, but they are not
  // the same act. Signing up starts an organization you own; signing in enters
  // ones you already belong to. The backend used to decide by looking for a
  // pending invite, which meant an invited member could never start a company
  // of their own — every way in led back to the one that invited them. The
  // intent now travels with the request.
  // The landing page's Get started and pricing buttons carry ?mode=signup.
  // Without reading it every route in opened on sign in, so there was no way
  // to reach sign up at all short of clicking the small toggle at the bottom.
  const [searchParams] = useSearchParams();
  const [mode, setMode] = useState<'signin' | 'signup'>(
    searchParams.get('mode') === 'signup' ? 'signup' : 'signin'
  );
  const isLoggingOut = useRef(false);
  const { capture, reset: resetAnalytics } = useAnalytics();

  // Safe capture wrapper
  const safeCapture = useCallback(async (event: string, properties?: Record<string, any>) => {
    try {
      await capture(event, properties);
    } catch (error) {
      console.error(`Analytics error:`, error);
    }
  }, [capture]);

  // Redirect authenticated users to appropriate page
  useEffect(() => {
    if (!authLoading && user && subscriptionStatus !== null) {
      // A pending workspace invite takes priority over the normal
      // subscription-based redirect, otherwise AcceptInvite.tsx's
      // "store token, send to /login, login will redirect back" flow
      // silently loses the invite: the user lands on /chat or /settings
      // instead and the accept-invite POST never fires.
      const pendingInviteToken = sessionStorage.getItem('pending_invite_token');
      if (pendingInviteToken) {
        sessionStorage.removeItem('pending_invite_token');
        navigate(`/invite/${pendingInviteToken}`, { replace: true });
        return;
      }

      // Always resolve the organization first. That screen selects silently
      // and forwards to chat when there is only one, which is the common case,
      // and it is what decides entitlement: a member of a paying company must
      // never be sent to /settings to start a duplicate trial.
      navigate('/select-organization', { replace: true });
    }
  }, [user, subscriptionStatus, authLoading, navigate]);

  const signInWithGoogle = useCallback(async () => {
    if (user || isSigningIn) return;

    setIsSigningIn(true);
    const provider = new GoogleAuthProvider();

    // Parked before the popup, because the auth listener that registers the
    // account fires long after this component's state is gone.
    sessionStorage.setItem('auth_intent', mode);

    try {
      await safeCapture('auth_attempt', { method: 'google', intent: mode });
      
      // Use popup for better mobile compatibility
      const result = await signInWithPopup(auth, provider);
      
      if (result?.user) {
        // Auto-detect if this is a new user
        const isNewUser = result.user.metadata.creationTime === result.user.metadata.lastSignInTime;
        
        await safeCapture(isNewUser ? 'user_signup' : 'user_signin', {
          method: 'google',
          isNewUser
        });
        
        // Navigation handled by useEffect above
        // New users will see /welcome, returning users go to /chat or /settings
        if (isNewUser) {
          navigate('/welcome', { replace: true });
        }
      }
    } catch (error) {
      console.error('Sign-in error:', error);
      
      // User-friendly error messages
      const err = error as any;
      let errorMessage = 'Failed to sign in. Please try again.';
      if (err.code === 'auth/popup-blocked') {
        errorMessage = 'Popup was blocked. Please allow popups and try again.';
      } else if (err.code === 'auth/popup-closed-by-user') {
        errorMessage = 'Sign-in cancelled.';
      } else if (err.code === 'auth/network-request-failed') {
        errorMessage = 'Network error. Please check your connection.';
      }
      
      await safeCapture('auth_error', {
        error: err.message,
        code: err.code
      });
      
      addToast(errorMessage, 'error');
    } finally {
      setIsSigningIn(false);
    }
  }, [user, isSigningIn, navigate, safeCapture, addToast]);

  const logOut = useCallback(async () => {
    // Prevent multiple logout attempts
    if (isLoggingOut.current) {
      console.log('[Logout] Logout already in progress');
      return;
    }
    
    isLoggingOut.current = true;
    
    try {
      // Clear storage. localStorage matters too: the active organization is
      // persisted there so a refresh does not re-ask, and leaving it behind
      // meant a deleted or switched account signed back in still pointing at
      // the previous account's organization.
      sessionStorage.clear();
      localStorage.removeItem('active_organization_id');
      
      // Clear cookies
      document.cookie.split(';').forEach(cookie => {
        const [name] = cookie.split('=');
        document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
      });
      
      // Sign out from Firebase
      await firebaseSignOut(auth);
      
      // Track logout (don't wait for it)
      safeCapture('user_logout', {
        timestamp: new Date().toISOString()
      }).catch(() => {});
      
      // Reset analytics
      resetAnalytics();
      
      // Redirect to login
      window.location.href = '/login';
      
    } catch (error) {
      console.error('[Logout] Error:', error);
      isLoggingOut.current = false;
      // Still redirect even if something fails
      window.location.href = '/login';
    }
  }, [safeCapture, resetAnalytics]);
  
  // Expose logOut via ref
  useImperativeHandle(ref, () => ({
    logOut
  }), [logOut]);

  if (authLoading) {
    return <div className="auth-page"><span className="auth-loading">Loading...</span></div>;
  }

  return (
    <div className="auth-page">
      {!user ? (
        <div className="auth-card">
          <h1 className="auth-title">Syntext</h1>
          {/* The two now do different things, so they say so. Signing up
              starts a company you own and pay for; signing in enters ones you
              already belong to. Somebody invited into a company can do both,
              and the wording has to make that make sense. */}
          <p className="auth-sub">
            {mode === 'signup'
              ? 'Start a company account. You will pick a plan and add a card.'
              : 'Sign in to a company you belong to.'}
          </p>
          <Button
            variant="outline"
            className="auth-google-btn w-full"
            onClick={signInWithGoogle}
            disabled={isSigningIn}
          >
            {isSigningIn ? (mode === 'signup' ? 'Creating account...' : 'Signing in...') : (
              <>
                <svg width="16" height="16" viewBox="0 0 18 18" fill="none">
                  <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" fill="#4285F4"/>
                  <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332C2.438 15.983 5.482 18 9 18z" fill="#34A853"/>
                  <path d="M3.964 10.707c-.18-.54-.282-1.117-.282-1.707 0-.593.102-1.17.282-1.709V4.958H.957C.347 6.173 0 7.548 0 9c0 1.452.348 2.827.957 4.042l3.007-2.335z" fill="#FBBC05"/>
                  <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.462.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
                </svg>
                {mode === 'signup' ? 'Sign up with Google' : 'Continue with Google'}
              </>
            )}
          </Button>
          <p className="auth-hint">
            {mode === 'signup' ? (
              <>
                Already have an account?{' '}
                <button type="button" className="auth-link" onClick={() => setMode('signin')}>
                  Sign in
                </button>
              </>
            ) : (
              <>
                Want your own company account?{' '}
                <button type="button" className="auth-link" onClick={() => setMode('signup')}>
                  Sign up
                </button>
              </>
            )}
          </p>
          <p className="auth-hint auth-hint-quiet">
            Joining a team? Use the invite link your colleague sent you.
          </p>
        </div>
      ) : (
        <Button onClick={logOut} disabled={isLoggingOut.current}>
          {isLoggingOut.current ? 'Signing out...' : 'Sign out'}
        </Button>
      )}
    </div>
  );
});

export default Auth;