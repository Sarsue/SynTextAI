import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { User } from 'firebase/auth'; // Import Firebase User type

// Define the type for UserSettings
interface UserSettings {
    comprehensionLevel: string;
    selectedLanguage: string;
}

// Define the type for UserContext
interface UserContextType {
    darkMode: boolean;
    toggleDarkMode: () => void;
    setDarkMode: (darkMode: boolean) => void;
    user: User | null; // Add Firebase User here
    setUser: (user: User | null) => void; // Add method to set user
    userSettings: UserSettings;
    setUserSettings: (settings: UserSettings) => void;
    isPollingFiles: boolean;
    setIsPollingFiles: (isPollingFile: boolean) => void;
    isPollingMessages: boolean;
    setIsPollingMessages: (isPollingMsg: boolean) => void;
    subscriptionStatus: string | null;
    setSubscriptionStatus: (status: string | null) => void;
    fetchSubscriptionStatus: () => void;
}

// Create the UserContext with initial values
const UserContext = createContext<UserContextType>({
    darkMode: false,
    toggleDarkMode: () => { },
    setDarkMode: () => { },
    user: null, // Initially, user is null
    setUser: () => { },
    userSettings: {
        comprehensionLevel: '',
        selectedLanguage: '',
    },
    setUserSettings: () => { },
    isPollingFiles: false,
    setIsPollingFiles: () => { },
    isPollingMessages: false,
    setIsPollingMessages: () => { },
    subscriptionStatus: null,
    setSubscriptionStatus: () => { },
    fetchSubscriptionStatus: () => { },
});

// Define UserProvider component
export const UserProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [darkMode, setDarkMode] = useState<boolean>(false);
    const [user, setUser] = useState<User | null>(null); // Manage Firebase user state
    const [userSettings, setUserSettings] = useState<UserSettings>({
        comprehensionLevel: 'Beginner',  // Default value set to 'Beginner'
        selectedLanguage: 'English',     // Default value set to 'English'
    });
    const [isPollingFiles, setIsPollingFiles] = useState<boolean>(false);
    const [isPollingMessages, setIsPollingMessages] = useState<boolean>(false);
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null);

    const toggleDarkMode = () => {
        setDarkMode((prevMode) => !prevMode);
    };

    // Fetch subscription status when the user is available
    useEffect(() => {
        if (user) { // Only fetch subscription if user is available
            fetchSubscriptionStatus();
        }
    }, [user]);

    const fetchSubscriptionStatus = async () => {
        try {
            const idToken = await user?.getIdToken(); // Get ID Token from Firebase User object
            if (!idToken) throw new Error('No token found');

            const response = await fetch('api/v1/subscriptions/status', {
                method: 'GET',
                headers: {
                    Authorization: `Bearer ${idToken}`,
                },
            });

            if (!response.ok) {
                throw new Error('Failed to fetch subscription status');
            }

            const data = await response.json();
            setSubscriptionStatus(data.subscription_status ?? null);
        } catch (error) {
            console.error('Error fetching subscription status:', error);
            setSubscriptionStatus(null);
        }
    };

    return (
        <UserContext.Provider
            value={{
                darkMode,
                toggleDarkMode,
                setDarkMode,
                user,
                setUser,
                userSettings,
                setUserSettings,
                isPollingFiles,
                setIsPollingFiles,
                isPollingMessages,
                setIsPollingMessages,
                subscriptionStatus,
                setSubscriptionStatus,
                fetchSubscriptionStatus,
            }}
        >
            {children}
        </UserContext.Provider>
    );
};

// Define useUserContext hook
export const useUserContext = (): UserContextType => {
    const context = useContext(UserContext);
    if (!context) {
        throw new Error('useUserContext must be used within a UserProvider');
    }
    return context;
};
