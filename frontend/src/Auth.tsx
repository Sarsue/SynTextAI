import React, { FC, useEffect } from 'react';
import {
    User as FirebaseUser,
    signOut,
    onAuthStateChanged,
    GoogleAuthProvider,
    signInWithPopup,
} from 'firebase/auth';
import { useNavigate } from 'react-router-dom';
import { auth } from './firebase';

interface AuthProps {
    setUser: React.Dispatch<React.SetStateAction<FirebaseUser | null>>;
}

const Auth: FC<AuthProps> = ({ setUser }) => {
    const navigate = useNavigate();

    useEffect(() => {
        // Check auth state
        const unsubscribe = onAuthStateChanged(auth, (user) => {
            if (user) {
                console.log('User already signed in:', user);
                setUser(user);
                navigate('/chat'); // Redirect to chat page
            }
        });

        return () => unsubscribe(); // Clean up the listener on unmount
    }, [setUser, navigate]);

    const signInWithGoogle = async () => {
        const provider = new GoogleAuthProvider();
        try {
            console.log('Opening Google sign-in popup...');
            const result = await signInWithPopup(auth, provider);
            const user = result.user;
            console.log('Sign-in successful:', user);
            setUser(user);
            navigate('/chat'); // Redirect to chat page
        } catch (err) {
            console.error('Sign-in error:', err);
        }
    };

    const logOut = async () => {
        try {
            await signOut(auth);
            console.log('User logged out');
            setUser(null);
        } catch (err) {
            console.error('Logout error:', err);
        }
    };

    return (
        <div className="auth-container">
            <button className="google-sign-in-button" onClick={signInWithGoogle}>
                Sign in with Google
            </button>
            {auth.currentUser && (
                <button className="logout-button" onClick={logOut}>
                    Log Out
                </button>
            )}
        </div>
    );
};

export default Auth;
