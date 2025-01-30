import React, { FC, useEffect, useState } from 'react';
import {
    signInWithPopup,
    signOut,
    onAuthStateChanged,
    GoogleAuthProvider
} from 'firebase/auth';
import { useNavigate } from 'react-router-dom';
import { auth } from './firebase';
import { useUserContext } from './UserContext';

const Auth: FC = () => {
    const navigate = useNavigate();
    const { setUser, user, subscriptionStatus, setSubscriptionStatus } = useUserContext();
    const [isLoading, setIsLoading] = useState(true);

    // Listen for authentication state changes
    useEffect(() => {
        const unsubscribe = onAuthStateChanged(auth, (authUser) => {
            if (authUser) {
                setUser(authUser); // Set user, triggers subscription fetch in UserContext
            } else {
                setUser(null);
                setSubscriptionStatus(null); // Ensure subscription status resets on logout
            }
            setIsLoading(false); // Move here to avoid indefinite loading
        });
        return () => unsubscribe();
    }, [setUser, setSubscriptionStatus]);

    // Handle navigation after authentication & subscription check
    useEffect(() => {
        if (user && subscriptionStatus !== null) {
            navigate(subscriptionStatus === 'active' ? '/chat' : '/settings');
        }
    }, [user, subscriptionStatus, navigate]);

    // Sign in with Google
    const signInWithGoogle = async () => {
        const provider = new GoogleAuthProvider();
        try {
            await signOut(auth); // Ensure fresh sign-in
            const result = await signInWithPopup(auth, provider);
            setUser(result.user); // Set user in context
        } catch (error) {
            console.error('Sign-in error:', error);
        }
    };

    // Log out function
    const logOut = async () => {
        try {
            await signOut(auth);
            setUser(null);
            setSubscriptionStatus(null); // Reset subscription status on logout
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
