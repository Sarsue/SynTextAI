import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import PaymentView from './PaymentView';
import DarkModeToggle from './DarkModeToggle';
import './SettingsPage.css'; // Import the CSS file
import { User } from 'firebase/auth';
import { useUserContext } from '../UserContext';
import { loadStripe, Stripe } from '@stripe/stripe-js';

interface SettingsPageProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null; // Adjust the user prop type
    subscriptionStatus: string | null;
}

const SettingsPage: React.FC<SettingsPageProps> = ({ stripePromise, user, subscriptionStatus }) => {
    const navigate = useNavigate();
    const { darkMode, setDarkMode, userSettings, setUserSettings } = useUserContext();
    const [subscriptionStatusLocal, setSubscriptionStatusLocal] = useState<string | null>(subscriptionStatus);

    // Language and comprehension level states
    const [selectedLanguage, setSelectedLanguage] = useState<string>(userSettings.selectedLanguage || '');
    const [comprehensionLevel, setComprehensionLevel] = useState<string>(userSettings.comprehensionLevel || '');

    // Update userSettings in context whenever any relevant state changes
    useEffect(() => {
        setUserSettings({
            ...userSettings,  // Preserving existing userSettings
            selectedLanguage,
            comprehensionLevel
        });
    }, [selectedLanguage, comprehensionLevel, setUserSettings]);

    const handleSubscriptionChange = (newStatus: string) => {
        setSubscriptionStatusLocal(newStatus);
    };

    const validLanguages = ['English', 'French', 'German', 'Spanish', 'Chinese', 'Japanese'];
    const validEducationLevels = ['Beginner', 'Intermediate', 'Advanced'];

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

                {/* Language Selection Section */}
                <div className="settings-section">
                    <h2 className="section-title">Language</h2>
                    <div className="section-content">
                        <select
                            value={selectedLanguage}
                            onChange={(e) => setSelectedLanguage(e.target.value)}
                        >
                            <option value="">Select a language</option>
                            {validLanguages.map((language) => (
                                <option key={language} value={language}>{language}</option>
                            ))}
                        </select>
                    </div>
                </div>

                {/* Comprehension Level Section */}
                <div className="settings-section">
                    <h2 className="section-title">Comprehension Level</h2>
                    <div className="section-content">
                        <select
                            value={comprehensionLevel}
                            onChange={(e) => setComprehensionLevel(e.target.value)}
                        >
                            <option value="">Select comprehension level</option>
                            {validEducationLevels.map((level) => (
                                <option key={level} value={level}>{level}</option>
                            ))}
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
