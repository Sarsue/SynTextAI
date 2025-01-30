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
}

const SettingsPage: React.FC<SettingsPageProps> = ({ stripePromise, user }) => {
    const navigate = useNavigate();
    const { darkMode, setDarkMode, userSettings, setUserSettings, subscriptionStatus, setUser } = useUserContext();

    // Language and comprehension level states
    const [selectedLanguage, setSelectedLanguage] = useState<string>(
        userSettings.selectedLanguage || 'English' // Default to 'English' if empty
    );
    const [comprehensionLevel, setComprehensionLevel] = useState<string>(
        userSettings.comprehensionLevel || 'Beginner' // Default to 'Beginner' if empty
    );

    // Update userSettings in context whenever any relevant state changes
    useEffect(() => {
        setUserSettings({
            ...userSettings,  // Preserving existing userSettings
            selectedLanguage,
            comprehensionLevel
        });
    }, [selectedLanguage, comprehensionLevel, setUserSettings]);

    // Log the subscription status every time it changes
    useEffect(() => {
        console.log("Current subscription status:", subscriptionStatus);
    }, [subscriptionStatus]);

    const handleDeleteAccount = async () => {
        const confirmed = window.confirm(
            "⚠️ WARNING: Deleting your account will permanently remove:\n\n" +
            "- Your payment information 💳\n" +
            "- Your chat history 💬\n" +
            "- Your uploaded files 📂\n" +
            "- Your account credentials 👤\n\n" +
            "This action is irreversible! Are you sure you want to proceed?"
        );

        if (!confirmed) return;

        if (!user) {
            alert("No user found.");
            return;
        }

        try {
            const idToken = await user.getIdToken();
            if (!idToken) {
                console.error('User token not available');
                alert("Authentication failed. Please try logging in again.");
                return;
            }

            const response = await fetch('/api/v1/users', {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${idToken}`,
                    'Content-Type': 'application/json',
                },
                mode: 'cors',
                credentials: 'include',
            });

            if (response.ok) {
                alert("✅ Your account has been successfully deleted.");
                setUser(null);  // Assuming setUser is available from your UserContext
                navigate('/');
            } else {
                const errorData = await response.json();
                console.error("Delete error:", errorData);
                alert(`❌ Failed to delete account: ${errorData.error || "Unknown error"}`);
            }
        } catch (error) {
            console.error("Error deleting account:", error);
            alert("⚠️ A network error occurred. Please try again later.");
        }
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
            <div className="settings-content">
                {/* Payment Section */}
                <div className="settings-section">
                    <h2 className="section-title">Payment</h2>
                    <div className="section-content">
                        <PaymentView
                            stripePromise={stripePromise}
                            user={user}
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

                {/* Account Management Section */}
                <h2 className="section-title text-red-500">Delete Account</h2>
                <p className="text-sm text-gray-600">
                    Deleting your account will permanently erase all of your data, including:
                </p>
                <ul className="list-disc text-gray-700 ml-5 my-2 text-sm">
                    <li>Payment details 💳</li>
                    <li>Chat history 💬</li>
                    <li>Uploaded files 📂</li>
                    <li>Account credentials 👤</li>
                </ul>
                <button
                    className="bg-red-500 text-white px-4 py-2 rounded-md hover:bg-red-700 transition duration-200"
                    onClick={handleDeleteAccount}
                >
                    Delete My Account
                </button>
            </div>
        </div>
    );
};

export default SettingsPage;
