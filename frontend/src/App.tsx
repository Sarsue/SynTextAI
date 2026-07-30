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
import SelectOrganization from './components/SelectOrganization';
import AnalyticsProvider from './components/AnalyticsProvider';

const stripePromise = loadStripe(import.meta.env.VITE_STRIPE_API_KEY || "");

const App: React.FC = () => {
    const { user, orgContext } = useUserContext();
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
                        {/* Which tenant am I in? Auto-resolves and skips itself
                            when the user belongs to exactly one organization. */}
                        <Route
                            path="/select-organization"
                            element={user ? <SelectOrganization /> : <Navigate to="/login" replace />}
                        />
                        <Route 
                            path="/welcome" 
                            element={user ? <Welcome /> : <Navigate to="/login" replace />} 
                        />
                        <Route
                            path="/chat"
                            element={
                                user ? (
                                    // Entitlement belongs to the active organization,
                                    // never to the individual. Staff have no subscription
                                    // of their own and were previously bounced to
                                    // /settings, which is what forced them through trial
                                    // signup for a company that already pays.
                                    // No organization chosen yet, so ask first.
                                    !orgContext ? (
                                        <Navigate to="/select-organization" replace />
                                    ) : orgContext.entitled ? (
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
