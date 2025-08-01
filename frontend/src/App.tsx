import React, { useCallback, ReactNode } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import type { FallbackProps } from './components/ErrorBoundary';
import { HashRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { useUserContext } from './UserContext';
import Home from './Home';
import Auth, { AuthRef } from './Auth';
import Welcome from './Welcome';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';
import AnalyticsProvider from './components/AnalyticsProvider';

const stripePromise = loadStripe(process.env.REACT_APP_STRIPE_API_KEY || "");

// Fallback UI for when an error occurs
const ErrorFallback: React.FC<FallbackProps> = ({ error, resetErrorBoundary }) => (
    <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        padding: '2rem',
        textAlign: 'center',
        backgroundColor: '#f8f9fa',
        color: '#212529'
    }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '1rem' }}>Something went wrong</h1>
        <p style={{ marginBottom: '2rem', maxWidth: '600px', color: '#6c757d' }}>
            We're sorry, but an unexpected error occurred. Our team has been notified.
        </p>
        <div style={{ display: 'flex', gap: '1rem' }}>
            <button
                onClick={resetErrorBoundary}
                style={{
                    padding: '0.75rem 1.5rem',
                    backgroundColor: '#0d6efd',
                    color: 'white',
                    border: 'none',
                    borderRadius: '0.375rem',
                    cursor: 'pointer',
                    fontSize: '1rem',
                    fontWeight: 500
                }}
            >
                Try again
            </button>
            <button
                onClick={() => window.location.reload()}
                style={{
                    padding: '0.75rem 1.5rem',
                    backgroundColor: '#f8f9fa',
                    color: '#0d6efd',
                    border: '1px solid #0d6efd',
                    borderRadius: '0.375rem',
                    cursor: 'pointer',
                    fontSize: '1rem',
                    fontWeight: 500
                }}
            >
                Reload page
            </button>
        </div>
        {process.env.NODE_ENV === 'development' && (
            <details style={{ marginTop: '2rem', textAlign: 'left', maxWidth: '800px', width: '100%' }}>
                <summary style={{ cursor: 'pointer', color: '#0d6efd', marginBottom: '0.5rem' }}>Error details</summary>
                <pre style={{
                    backgroundColor: '#f1f3f5',
                    padding: '1rem',
                    borderRadius: '0.375rem',
                    overflow: 'auto',
                    maxHeight: '300px',
                    fontSize: '0.875rem',
                    color: '#212529'
                }}>
                    {error.stack || error.message}
                </pre>
            </details>
        )}
    </div>
);

const App: React.FC = () => {
    const { user, subscriptionStatus } = useUserContext();
    const authRef = React.useRef<AuthRef>(null);

    return (
        <ErrorBoundary 
            FallbackComponent={ErrorFallback}
            onError={(error: Error, errorInfo: React.ErrorInfo) => {
                // Log error to your error reporting service
                console.error('App error boundary caught:', error, errorInfo);
            }}
        >
            <Elements stripe={stripePromise}>
                <AnalyticsProvider config={{
                    userId: user?.uid,
                    debugMode: process.env.NODE_ENV === 'development',
                }}>
                    <Router>
                        <div className="app-container">
                            <Routes>
                                <Route path="/" element={
                                    <ErrorBoundary FallbackComponent={ErrorFallback}>
                                        <Home />
                                    </ErrorBoundary>
                                } />
                                <Route path="/login" element={
                                    <ErrorBoundary FallbackComponent={ErrorFallback}>
                                        <Auth ref={authRef} />
                                    </ErrorBoundary>
                                } />
                                <Route 
                                    path="/welcome" 
                                    element={
                                        <ErrorBoundary FallbackComponent={ErrorFallback}>
                                            {user ? <Welcome /> : <Navigate to="/login" replace />}
                                        </ErrorBoundary>
                                    } 
                                />
                                <Route
                                    path="/chat"
                                    element={
                                        <ErrorBoundary FallbackComponent={ErrorFallback}>
                                            {user ? (
                                                subscriptionStatus === 'active' || subscriptionStatus === 'trialing' ? (
                                                    <ChatApp 
                                                        user={user} 
                                                        onLogout={() => authRef.current?.logOut()}
                                                    />
                                                ) : (
                                                    <Navigate to="/settings" replace />
                                                )
                                            ) : (
                                                <Navigate to="/login" replace />
                                            )}
                                        </ErrorBoundary>
                                    }
                                />
                                <Route
                                    path="/settings"
                                    element={
                                        <ErrorBoundary FallbackComponent={ErrorFallback}>
                                            {user ? <SettingsPage stripePromise={stripePromise} user={user} /> : <Navigate to="/login" replace />}
                                        </ErrorBoundary>
                                    }
                                />
                                <Route path="*" element={
                                    <ErrorBoundary FallbackComponent={ErrorFallback}>
                                        <Navigate to="/" />
                                    </ErrorBoundary>
                                } />
                            </Routes>
                        </div>
                    </Router>
                </AnalyticsProvider>
            </Elements>
        </ErrorBoundary>
    );
};

// Use Auth component directly with ref

export default App;
