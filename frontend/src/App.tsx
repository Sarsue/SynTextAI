import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
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
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null); // Type explicitly
    const { darkMode } = useDarkMode();
    const navigate = useNavigate();

    useEffect(() => {
        // Fetch subscription status once the user is logged in
        if (user) {
            fetchSubscriptionStatus();
        }
    }, [user]);

    const fetchSubscriptionStatus = async () => {
        // Make an API call to fetch the subscription status
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

    // Handle redirection in a useEffect
    useEffect(() => {
        if (subscriptionStatus !== 'active' && subscriptionStatus !== null) {
            //navigate('/settings?tab=payment'); // Redirect to payment settings tab if not active
            navigate('/settings')
        }
    }, [subscriptionStatus, navigate]); // Dependency on subscriptionStatus and navigate

    return (
        <DarkModeProvider> {/* Wrap your application with DarkModeProvider */}
            <Elements stripe={stripePromise}>
                <Router>
                    <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
                        <Routes>
                            <Route path="/" element={<Home />} />
                            <Route
                                path="/login"
                                element={<Auth setUser={setUser} />}
                            />
                            <Route
                                path="/chat"
                                element={
                                    subscriptionStatus === 'active' ? (
                                        <ChatApp
                                            user={user}
                                            onLogout={() => setUser(null)}
                                            subscriptionStatus={subscriptionStatus}
                                        />
                                    ) : (
                                        // Render a placeholder until the redirect happens
                                        <div>Redirecting to settings...</div>
                                    )
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
