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
import { useUserContext } from './UserContext';  // Import UserContext to access darkMode

interface AuthProps {
    setUser: React.Dispatch<React.SetStateAction<FirebaseUser | null>>;
}

const Auth: FC<AuthProps> = ({ setUser }) => {
    const navigate = useNavigate();
    const { darkMode } = useUserContext();

    useEffect(() => {
        const unsubscribe = onAuthStateChanged(auth, async (user) => {
            setUser(user);
            if (user) {
                console.log('User is signed in:', user);
                await registerUserInBackend(user); // Register the user in backend
                navigate('/chat');
            }
        });

        return () => unsubscribe();
    }, [setUser, navigate]);

    const signInWithGoogle = async () => {
        const provider = new GoogleAuthProvider();
        try {
            console.log('Signing out before signing in...');
            await signOut(auth); // Ensure a clean sign-in
            console.log('Opening Google sign-in popup...');
            const result = await signInWithPopup(auth, provider);
            const user = result.user;

            if (user) {
                console.log('Sign-in successful:', user);
                await registerUserInBackend(user); // Register the user in backend
                setUser(user);
                navigate('/chat');
            }
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

    const registerUserInBackend = async (user: FirebaseUser) => {
        try {
            const idToken = await user.getIdToken();
            console.log('User ID token:', idToken);

            const response = await fetch(`/api/v1/users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
            });

            if (!response.ok) {
                throw new Error(`Failed to register user: ${response.statusText}`);
            }

            console.log('User successfully registered in backend:', await response.json());
        } catch (error) {
            console.error('Error registering user in backend:', error);
        }
    };

    return (
        <div className={`auth-container ${darkMode ? 'dark-mode' : ''}`}> {/* Bind to darkMode */}
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
