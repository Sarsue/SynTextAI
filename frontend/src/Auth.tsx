// Auth.tsx
import React, { FC, useEffect } from 'react';
import {
    User as FirebaseUser,
    signInWithPopup,
    signOut,
    onAuthStateChanged,
    GoogleAuthProvider,
} from 'firebase/auth';
import { useNavigate } from 'react-router-dom';
import { auth } from './firebase';

interface AuthProps {
    setUser: React.Dispatch<React.SetStateAction<FirebaseUser | null>>;
}

const Auth: FC<AuthProps> = ({ setUser }) => {
    const navigate = useNavigate();

    useEffect(() => {
        const unsubscribe = onAuthStateChanged(auth, (user) => {
            setUser(user);
            if (user) {
                // Call the API after successful sign-in
                callApiWithToken(user);
                navigate('/chat');
            }
        });

        return () => unsubscribe();
    }, [setUser, navigate]);

    const signInWithGoogle = async () => {
        const provider = new GoogleAuthProvider();
        try {
            await signOut(auth); // Sign out before signing in again
            const result = await signInWithPopup(auth, provider);
            const user = result.user;
            if (user) {
                // Call the API after successful sign-in
                callApiWithToken(user);
            }
        } catch (err) {
            console.error(err);
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

            // Now you can use the idToken to make API requests to your Flask API
            // Example: Make a POST request to your API endpoint with the idToken
            const response = await fetch(`api/v1/users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
                // Add any body or other options as needed
            });

            // Handle the API response as needed
            console.log('API response:', response);
        } catch (error) {
            console.error('Error calling API:', error);
        }
    };

    return (
        <div>
            <button className="google-sign-in-button" onClick={signInWithGoogle}>
                Sign in with Google
            </button>
            {auth.currentUser !== null && (
                <button onClick={logOut}>Log Out</button>
            )}
        </div>
    );
};

export default Auth;
