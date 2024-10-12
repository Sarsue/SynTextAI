// Auth.tsx
import React, { FC, useEffect } from 'react';
import {
    User as FirebaseUser,
    signInWithPopup,
    signOut,
    onAuthStateChanged,
    GoogleAuthProvider,
    signInWithRedirect,
    getRedirectResult,
} from 'firebase/auth';
import { useNavigate } from 'react-router-dom';
import { auth } from './firebase';

interface AuthProps {
    setUser: React.Dispatch<React.SetStateAction<FirebaseUser | null>>;
}

const Auth: FC<AuthProps> = ({ setUser }) => {
    const navigate = useNavigate();

    // Handle Firebase auth state and redirect result for mobile
    useEffect(() => {
        const handleRedirectResult = async () => {
            const result = await getRedirectResult(auth); // Handle sign-in result
            if (result?.user) {
                setUser(result.user);
                await callApiWithToken(result.user);
                navigate('/chat');
            }
        };
        handleRedirectResult();

        const unsubscribe = onAuthStateChanged(auth, (user) => {
            setUser(user);
            if (user) {
                callApiWithToken(user).then(() => navigate('/chat'));
            }
        });

        return () => unsubscribe();
    }, [setUser, navigate]);

    // Use redirect sign-in for mobile compatibility
    const signInWithGoogle = async () => {
        const provider = new GoogleAuthProvider();
        try {
            await signOut(auth);
            await signInWithRedirect(auth, provider); // Use redirect instead of popup
        } catch (err) {
            console.error('Sign-in error:', err);
        }
    };

    const logOut = async () => {
        try {
            await signOut(auth);
            setUser(null);
        } catch (err) {
            console.error(err);
        }
    };

    const callApiWithToken = async (user: FirebaseUser) => {
        try {
            const idToken = await user.getIdToken();
            const response = await fetch(`api/v1/users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
            });
            console.log('API response:', response);
        } catch (error) {
            console.error('Error calling API:', error);
        }
    };

    return (
        <div className="auth-container">
            <button className="google-sign-in-button" onClick={signInWithGoogle}>
                Sign in with Google
            </button>
            {auth.currentUser && (
                <button className="logout-button" onClick={logOut}>Log Out</button>
            )}
        </div>
    );
};

export default Auth;
