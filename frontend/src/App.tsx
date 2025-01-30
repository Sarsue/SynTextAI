import React from 'react';
import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { useUserContext } from './UserContext';
import Home from './Home';
import Auth from './Auth';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';

console.log("Stripe API Key:", process.env.REACT_APP_STRIPE_API_KEY);
const stripePromise = loadStripe(process.env.REACT_APP_STRIPE_API_KEY || "");

const App: React.FC = () => {
    const { user, subscriptionStatus } = useUserContext();

    return (
        <Elements stripe={stripePromise}>
            <Router>
                <div className="app-container">
                    <Routes>
                        <Route path="/" element={<Home />} />
                        <Route path="/login" element={<Auth />} />
                        <Route
                            path="/chat"
                            element={
                                user ? (
                                    subscriptionStatus === 'active' ? (
                                        <ChatApp user={user} onLogout={() => { }} />
                                    ) : (
                                        <Navigate to="/settings" replace />
                                    )
                                ) : (
                                    <Navigate to="/login" replace />
                                )
                            }
                        />
                        <Route
                            path="/settings"
                            element={user ? <SettingsPage stripePromise={stripePromise} user={user} /> : <Navigate to="/login" replace />}
                        />
                    </Routes>
                </div>
            </Router>
        </Elements>
    );
};

export default App;
