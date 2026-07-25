import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import PaymentView from './PaymentView';
import DarkModeToggle from './DarkModeToggle';
import './SettingsPage.css'; // Import the CSS file
import { User } from 'firebase/auth';
import { useUserContext } from '../UserContext';
import { Stripe } from '@stripe/stripe-js';
import { Button } from '@/components/ui/button';

interface SettingsPageProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null; // Adjust the user prop type
}

const SettingsPage: React.FC<SettingsPageProps> = ({ stripePromise, user }) => {
    const navigate = useNavigate();
    const { darkMode, setDarkMode, subscriptionStatus, setUser } = useUserContext();
    const didRedirect = useRef(false);

    useEffect(() => {
        if (!didRedirect.current && (subscriptionStatus === 'active' || subscriptionStatus === 'trialing')) {
            didRedirect.current = true;
            navigate('/chat', { replace: true });
        }
    }, [subscriptionStatus, navigate]);

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


    return (
        <div className={`settings-container ${darkMode ? 'dark-mode' : ''}`}>
            {/* Close Button */}
            <Button
                variant="ghost"
                size="icon-sm"
                className="close-button"
                onClick={() => navigate('/chat')}
            >
                <X className="size-4" />
            </Button>

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

                {/* Theme Section */}
                <div className="settings-section">
                    <h2 className="section-title">Theme</h2>
                    <div className="section-content">
                        <DarkModeToggle darkMode={darkMode} setDarkMode={setDarkMode} />
                    </div>
                </div>

                {/* Account Management Section */}
                <div className="settings-section">
                    <h2 className="section-title text-destructive">Delete Account</h2>
                    <div className="section-content">
                        <p className="text-sm text-muted-foreground">
                            Deleting your account will permanently erase all of your data, including:
                        </p>
                        <ul className="list-disc ml-5 text-sm text-muted-foreground">
                            <li>Payment details</li>
                            <li>Chat history</li>
                            <li>Uploaded files</li>
                            <li>Account credentials</li>
                        </ul>
                        <Button variant="destructive" onClick={handleDeleteAccount} className="w-fit">
                            Delete My Account
                        </Button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SettingsPage;
