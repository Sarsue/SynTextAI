import React, { useCallback } from 'react';
import { HashRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { useUserContext } from './UserContext';
import Home from './Home';
import Auth, { AuthRef } from './Auth';
import SignUp from './SignUp';
import Welcome from './Welcome';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';
import AcceptInvite from './components/AcceptInvite';
import SelectOrganization from './components/SelectOrganization';
import AnalyticsProvider from './components/AnalyticsProvider';

const stripePromise = loadStripe(import.meta.env.VITE_STRIPE_API_KEY || "");

const App: React.FC = () => {
    const { user, orgContext, authLoading, activeOrganizationId } = useUserContext();
    const authRef = React.useRef<AuthRef>(null);

    // Guards must not decide while the answer is still arriving.
    //
    // They read `user` and `orgContext` directly, both of which are null for the
    // first moments after a sign-in or sign-up: Firebase reports the account,
    // then the backend registers it, then the organization resolves. In that
    // window every guarded route redirected somewhere, and the redirect target
    // redirected back as the next value landed — the bounce. Two states are
    // "still loading", not "no":
    //
    //   authLoading                         Firebase and registration in flight
    //   activeOrganizationId && !orgContext an organization is chosen, its
    //                                       context has not arrived yet
    const resolving = authLoading || (activeOrganizationId != null && !orgContext);

    const waiting = (
        <div className="auth-page">
            <span className="auth-loading">Loading...</span>
        </div>
    );

    /** Render `element` once we know who the user is, rather than guessing. */
    const requireUser = (element: React.ReactNode) => {
        if (resolving) return waiting;
        return user ? <>{element}</> : <Navigate to="/login" replace />;
    };

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
                        {/* Its own route, not a mode on the sign-in page.
                            Signing in enters a company you belong to; signing
                            up creates one you own. Sharing a screen meant an
                            already-signed-in person saw only a sign out button
                            and could not reach sign up at all. */}
                        <Route path="/signup" element={<SignUp />} />
                        {/* Which tenant am I in? Auto-resolves and skips itself
                            when the user belongs to exactly one organization. */}
                        <Route
                            path="/select-organization"
                            element={requireUser(<SelectOrganization />)}
                        />
                        <Route path="/welcome" element={requireUser(<Welcome />)} />
                        <Route
                            path="/chat"
                            element={
                                resolving ? waiting : user ? (
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
                            element={
                                resolving
                                    ? waiting
                                    : user
                                        ? <SettingsPage stripePromise={stripePromise} user={user} />
                                        : <Navigate to="/login" replace />
                            }
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
