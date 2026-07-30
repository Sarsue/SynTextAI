import React, { useCallback } from 'react';
import { HashRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { useUserContext } from './UserContext';
import Home from './Home';
import Auth, { AuthRef } from './Auth';
import Welcome from './Welcome';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';
import AcceptInvite from './components/AcceptInvite';
import AnalyticsProvider from './components/AnalyticsProvider';

const stripePromise = loadStripe(import.meta.env.VITE_STRIPE_API_KEY || "");

const App: React.FC = () => {
    const { user, isEntitled } = useUserContext();
    const authRef = React.useRef<AuthRef>(null);

    return (
        <Elements stripe={stripePromise}>
            <AnalyticsProvider config={{
                userId: user?.uid,
                debugMode: import.meta.env.DEV,
            }}>
                <Router>
                    <div className="app-container">
                        <Routes>
                        <Route path="/" element={<Home />} />
                        <Route path="/login" element={<Auth ref={authRef} />} />
                        <Route 
                            path="/welcome" 
                            element={user ? <Welcome /> : <Navigate to="/login" replace />} 
                        />
                        <Route
                            path="/chat"
                            element={
                                user ? (
                                    // Gate on entitlement, not on this user's own
                                    // subscription. Staff of a paid workspace have no
                                    // subscription of their own and were bounced to
                                    // /settings, which is what forced them through trial
                                    // signup for an organization that already pays.
                                    isEntitled ? (
                                        <ChatApp
                                            user={user}
                                            onLogout={() => authRef.current?.logOut()}
                                        />
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

                        <Route path="/invite/:token" element={<AcceptInvite />} />
                        <Route path="*" element={<Navigate to="/" />} />
                    </Routes>
                    </div>
                </Router>
            </AnalyticsProvider>
        </Elements>
    );
};

// Use Auth component directly with ref

export default App;
