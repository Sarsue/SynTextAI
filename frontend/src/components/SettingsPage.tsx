import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import PaymentView from './PaymentView';
import DarkModeToggle from './DarkModeToggle';
import './SettingsPage.css'; // Import the CSS file
import { User } from 'firebase/auth';
import { useUserContext } from '../UserContext';
// Remove this line if not used
import { loadStripe, Stripe } from '@stripe/stripe-js';

// Removed loadStripe import as it's not used

interface SettingsPageProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null; // Adjust the user prop type
    subscriptionStatus: string | null;
}

const SettingsPage: React.FC<SettingsPageProps> = ({ stripePromise, user, subscriptionStatus }) => {
    const navigate = useNavigate();
    const { darkMode, setDarkMode, userSettings, setUserSettings } = useUserContext();
    const [subscriptionStatusLocal, setSubscriptionStatusLocal] = useState<string | null>(subscriptionStatus);
    const [explanationLevel, setExplanationLevel] = useState<string>(userSettings.explanationLevel || '');
    const [numberOfAnswers, setNumberOfAnswers] = useState<number>(userSettings.numberOfAnswers || 1); // Default to 1

    // Update userSettings in context whenever explanationLevel or numberOfAnswers changes
    useEffect(() => {
        setUserSettings({
            ...userSettings,  // Preserving existing userSettings
            explanationLevel,
            numberOfAnswers
        });
    }, [explanationLevel, numberOfAnswers, setUserSettings]);

    const handleSubscriptionChange = (newStatus: string) => {
        setSubscriptionStatusLocal(newStatus);
    };

    return (
        <div className={`settings-container ${darkMode ? 'dark-mode' : ''}`}>
            {/* Close Button */}
            <button className="close-button" onClick={() => navigate('/chat')}>
                ❌
            </button>

            {/* Settings Content */}
            <div className={`settings-content ${darkMode ? 'dark-mode' : ''}`}>
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

                {/* Explanation Level Section */}
                <div className="settings-section">
                    <h2 className="section-title">Explanation Level</h2>
                    <div className="section-content">
                        <select
                            value={explanationLevel}
                            onChange={(e) => setExplanationLevel(e.target.value)}
                        >
                            <option value="">Select explanation level</option>
                            <option value="child">Child</option>
                            <option value="teen">Teen</option>
                            <option value="college student">College Student</option>
                            <option value="grad student">Grad Student</option>
                            <option value="expert">Expert</option>
                        </select>
                    </div>
                </div>

                {/* Number of Answers Section */}
                <div className="settings-section">
                    <h2 className="section-title">Number of Answers</h2>
                    <div className="section-content">
                        <select
                            value={numberOfAnswers}
                            onChange={(e) => setNumberOfAnswers(Number(e.target.value))}
                        >
                            <option value={1}>1</option>
                            <option value={2}>2</option>
                            <option value={3}>3</option>
                            <option value={4}>4</option>
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
            </div>
        </div>
    );
};

export default SettingsPage;
