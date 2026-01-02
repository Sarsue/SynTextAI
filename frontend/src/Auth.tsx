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
  const { user, subscriptionStatus, authLoading } = useUserContext();
  const [isSigningIn, setIsSigningIn] = useState(false);
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
      const targetPath = subscriptionStatus === 'active' || subscriptionStatus === 'trialing' 
        ? '/chat' 
        : '/settings';
      navigate(targetPath, { replace: true });
    }
  }, [user, subscriptionStatus, authLoading, navigate]);

  const signInWithGoogle = useCallback(async () => {
    if (user || isSigningIn) return;

    setIsSigningIn(true);
    const provider = new GoogleAuthProvider();
    
    try {
      await safeCapture('auth_attempt', { method: 'google' });
      
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
      
      alert(errorMessage);
    } finally {
      setIsSigningIn(false);
    }
  }, [user, isSigningIn, navigate, safeCapture]);

  const logOut = useCallback(async () => {
    // Prevent multiple logout attempts
    if (isLoggingOut.current) {
      console.log('[Logout] Logout already in progress');
      return;
    }
    
    isLoggingOut.current = true;
    
    try {
      // Clear storage
      sessionStorage.clear();
      
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
    return (
      <div className="auth-container" style={{ 
        display: 'flex', 
        justifyContent: 'center', 
        alignItems: 'center',
        minHeight: '100vh',
        fontSize: '1.2rem',
        color: 'var(--text-color)'
      }}>
        Loading...
      </div>
    );
  }

  return (
    <div className="auth-container" style={{
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      padding: '20px'
    }}>
      {!user ? (
        <div style={{ textAlign: 'center', maxWidth: '400px' }}>
          <h1 style={{ marginBottom: '10px', fontSize: '2rem' }}>Welcome to SynText AI</h1>
          <p style={{ marginBottom: '30px', color: 'var(--text-secondary)', fontSize: '1rem' }}>
            Sign in to continue
          </p>
          
          <button 
            onClick={signInWithGoogle}
            disabled={isSigningIn}
            style={{
              background: 'var(--accent-color)',
              color: 'white',
              border: 'none',
              padding: '12px 24px',
              borderRadius: '8px',
              cursor: isSigningIn ? 'not-allowed' : 'pointer',
              fontWeight: 600,
              fontSize: '1rem',
              opacity: isSigningIn ? 0.7 : 1,
              transition: 'all 0.2s ease',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              margin: '0 auto'
            }}
          >
            {isSigningIn ? (
              'Signing in...'
            ) : (
              <>
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" fill="#4285F4"/>
                  <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332C2.438 15.983 5.482 18 9 18z" fill="#34A853"/>
                  <path d="M3.964 10.707c-.18-.54-.282-1.117-.282-1.707 0-.593.102-1.17.282-1.709V4.958H.957C.347 6.173 0 7.548 0 9c0 1.452.348 2.827.957 4.042l3.007-2.335z" fill="#FBBC05"/>
                  <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.462.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
                </svg>
                Continue with Google
              </>
            )}
          </button>
          
          <p style={{ 
            marginTop: '20px', 
            fontSize: '0.85rem', 
            color: 'var(--text-secondary)' 
          }}>
            New users will be automatically signed up
          </p>
        </div>
      ) : (
        <button 
          onClick={logOut}
          disabled={isLoggingOut.current}
          style={{
            background: 'var(--accent-color)',
            color: 'white',
            border: 'none',
            padding: '10px 20px',
            borderRadius: '6px',
            cursor: isLoggingOut.current ? 'not-allowed' : 'pointer',
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