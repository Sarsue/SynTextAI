import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import Home from './Home';
import Auth from './Auth';
import ChatApp from './components/ChatApp';
import SettingsPage from './components/SettingsPage';
import { User as FirebaseUser } from 'firebase/auth';
import { Elements } from '@stripe/react-stripe-js';
import { loadStripe } from '@stripe/stripe-js';
import { UserProvider, useUserContext } from './UserContext';

const stripePromise = loadStripe(process.env.REACT_APP_STRIPE_API_KEY);

const App: React.FC = () => {
    const [user, setUser] = useState<FirebaseUser | null>(null);
    const { darkMode } = useUserContext(); // Get subscriptionStatus from context


    return (
        <UserProvider>
            <Elements stripe={stripePromise}>
                <Router>
                    <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
                        <Routes>
                            <Route path="/" element={<Home />} />
                            <Route path="/login" element={<Auth setUser={setUser} />} />
                            <Route
                                path="/chat"
                                element={<ChatApp user={user} onLogout={() => setUser(null)} />}
                            />
                            <Route
                                path="/settings"
                                element={<SettingsPage stripePromise={stripePromise} user={user} />}
                            />
                        </Routes>
                    </div>
                </Router>
            </Elements>
        </UserProvider>
    );
};

export default App;
