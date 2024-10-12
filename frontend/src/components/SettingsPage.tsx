import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PaymentView from './PaymentView';
import DarkModeToggle from './DarkModeToggle';
import './SettingsPage.css'; // Import the CSS file
import { User } from 'firebase/auth';
import { useDarkMode } from '../DarkModeContext';
import { loadStripe, Stripe } from '@stripe/stripe-js';

interface SettingsPageProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null; // Adjust the user prop type
    subscriptionStatus: string | null;
}

const SettingsPage: React.FC<SettingsPageProps> = ({ stripePromise, user, subscriptionStatus }) => {
    const navigate = useNavigate();
    const { darkMode, setDarkMode } = useDarkMode();
    const [subscriptionStatusLocal, setSubscriptionStatusLocal] = useState<string | null>(subscriptionStatus);

    const handleSubscriptionChange = (newStatus: string) => {
        setSubscriptionStatusLocal(newStatus);
    };
    const [beliefSystem, setBeliefSystem] = useState<string>('Spiritual');
    const [demographic, setDemographic] = useState<string>('Millennials');

    return (
        <div className={`settings-container ${darkMode ? 'dark-mode' : ''}`}>
            {/* Close Button */}
            <button className="close-button" onClick={() => navigate('/chat')}>
                ❌
            </button>

            {/* Settings Content */}
            <div className={`settings-content ${darkMode ? 'dark-mode' : ''}`}>

                {/* Belief System Section */}
                <div className="settings-section">
                    <h2 className="section-title">Belief System</h2>
                    <div className="section-content">
                        <select
                            value={beliefSystem}
                            onChange={(e) => setBeliefSystem(e.target.value)}
                        >
                            <option value="Spiritual">Spiritual</option>
                            <option value="Secular">Secular</option>
                            <option value="Agnostic">Agnostic</option>
                        </select>
                    </div>
                </div>

                {/* Demographic Section */}
                <div className="settings-section">
                    <h2 className="section-title">Demographic</h2>
                    <div className="section-content">
                        <select
                            value={demographic}
                            onChange={(e) => setDemographic(e.target.value)}
                        >
                            <option value="Silent Generation">Silent Generation: 1928-1945</option>
                            <option value="Baby Boomers">Baby Boomers: 1946-1964</option>
                            <option value="Generation X">Generation X: 1965-1980</option>
                            <option value="Millennials">Millennials: 1981-1996</option>
                            <option value="Generation Z">Generation Z: 1997-2012</option>
                        </select>
                    </div>
                </div>

                {/* Theme Section */}
                <div className="settings-section">
                    <h2 className="section-title">Theme</h2>
                    <div className="section-content">
                        <DarkModeToggle darkMode={darkMode} setDarkMode={setDarkMode} />
                    </div>
                </div>

                {/* Payment Section */}
                <div className="settings-section">
                    <h2 className="section-title">Payment</h2>
                    <div className="section-content">
                        <PaymentView
                            stripePromise={stripePromise}
                            user={user}
                            subscriptionStatus={subscriptionStatusLocal}
                            onSubscriptionChange={handleSubscriptionChange}
                            darkMode={darkMode}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SettingsPage;
