import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom'; // Import Navigate for redirects
import Home from './Home';
import Auth from './Auth';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';

import { User as FirebaseUser } from 'firebase/auth';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { DarkModeProvider, useDarkMode } from './DarkModeContext';

const stripePromise = loadStripe(process.env.REACT_APP_STRIPE_API_KEY);

const App: React.FC = () => {
    const [user, setUser] = useState<FirebaseUser | null>(null);
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null); // Ensure subscriptionStatus is a string or null
    const { darkMode } = useDarkMode();

    useEffect(() => {
        if (user) {
            fetchSubscriptionStatus();
        }
        if (darkMode) {
            document.body.classList.add('dark-mode');
        } else {
            document.body.classList.remove('dark-mode');
        }

    }, [user, darkMode]);

    const fetchSubscriptionStatus = async () => {
        const idToken = await user?.getIdToken();
        const response = await fetch(`api/v1/subscriptions/status`, {
            method: 'GET',
            headers: {
                Authorization: `Bearer ${idToken}`,
                mode: 'cors',
            },
        });
        const data = await response.json();
        setSubscriptionStatus(data.subscription_status ?? null);
    };

    return (
        <DarkModeProvider>
            <Elements stripe={stripePromise}>
                <Router>
                    <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
                        <Routes>
                            <Route path="/" element={<Home />} />
                            <Route
                                path="/login"
                                element={<Auth setUser={setUser} />}
                            />
                            {/* <Route
                                path="/chat"
                                element={
                                    subscriptionStatus === null ? (
                                        <Navigate to="/settings" replace />
                                    ) : subscriptionStatus === 'active' ? (
                                        <ChatApp
                                            user={user}
                                            onLogout={() => setUser(null)}
                                            subscriptionStatus={subscriptionStatus}
                                        />
                                    ) : (
                                        <Navigate to="/settings" replace />
                                    )
                                }
                            /> */}
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
                    </div>
                </Router>
            </Elements>
        </DarkModeProvider>
    );
};

export default App;
