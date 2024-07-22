// App.tsx
import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import Home from './Home';
import Auth from './Auth';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';

import { User as FirebaseUser } from 'firebase/auth';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { DarkModeProvider, useDarkMode } from './DarkModeContext'; // Import useDarkMode hook

const stripePromise = loadStripe(process.env.REACT_APP_STRIPE_API_KEY); // Replace with your actual public key

const App: React.FC = () => {
    const [user, setUser] = useState<FirebaseUser | null>(null);
    const [subscriptionStatus, setSubscriptionStatus] = useState(null);
    const { darkMode } = useDarkMode();



    useEffect(() => {
        // Fetch subscription status once the user is logged in
        if (user) {
            fetchSubscriptionStatus();
        }
    }, [user]);
    const fetchSubscriptionStatus = async () => {
        // Make an API call to fetch the subscription status
        // Example:
        const idToken = await user?.getIdToken()
        const response = await fetch(`${process.env.REACT_APP_API_BASE_URL}/api/v1/subscriptions/status`, {
            method: 'GET',
            headers: {
                Authorization: `Bearer ${idToken}`,
                mode: 'cors',
            },
        });
        const data = await response.json();
        console.log(data.subscription_status); // Access as 'subscription_status'
        setSubscriptionStatus(data.subscription_status ?? null);

        // For demonstration purposes, let's assume the subscription status is 'active'
    };
    return (
        <DarkModeProvider> {/* Wrap your application with DarkModeProvider */}
            <Elements stripe={stripePromise}>
                <Router>
                    <Routes>
                        <Route path="/" element={<Home />} />
                        <Route
                            path="/login"
                            element={<Auth setUser={setUser} />}
                        />
                        <Route
                            path="/chat"
                            element={
                                <ChatApp
                                    user={user}
                                    onLogout={() => setUser(null)}
                                    subscriptionStatus={subscriptionStatus}

                                />
                            }
                        />
                        <Route
                            path="/settings"
                            element={
                                <SettingsPage
                                    stripePromise={stripePromise}
                                    user={user}
                                    subscriptionStatus={subscriptionStatus}
                                />
                            }
                        />
                    </Routes>
                </Router>
            </Elements>
        </DarkModeProvider>
    );
};

export default App;
