import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom'; // Import Navigate for redirects
import Home from './Home';
import Auth from './Auth';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';
import { Link } from 'react-router-dom';
import { User as FirebaseUser } from 'firebase/auth';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { UserProvider, useUserContext } from './UserContext';


const stripePromise = loadStripe(process.env.REACT_APP_STRIPE_API_KEY);

const App: React.FC = () => {
    const [user, setUser] = useState<FirebaseUser | null>(null);
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null); // Ensure subscriptionStatus is a string or null
    const { darkMode } = useUserContext();

    useEffect(() => {
        if (user) {
            fetchSubscriptionStatus();
        }

    }, [user]);


    const fetchSubscriptionStatus = async () => {
        const idToken = await user?.getIdToken();
        const response = await fetch(`api/v1/subscriptions/status`, {
            method: 'GET',
            headers: {
                Authorization: `Bearer ${idToken}`,
            },

        });
        const data = await response.json();
        setSubscriptionStatus(data.subscription_status ?? null);
    };

    return (
        <UserProvider>
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
        </UserProvider>
    );
};

export default App;
