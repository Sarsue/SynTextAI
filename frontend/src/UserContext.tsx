import React, { createContext, useContext, useState, ReactNode } from 'react';

// Define the type for UserSettings
interface UserSettings {
    gender: string;
    demographic: string;
    beliefSystem: string;
}

// Define the type for UserContext
interface UserContextType {
    darkMode: boolean;
    toggleDarkMode: () => void;
    setDarkMode: (darkMode: boolean) => void; // Add setDarkMode function
    userSettings: UserSettings; // Add userSettings to context type
    setUserSettings: (settings: UserSettings) => void; // Add setUserSettings function
}

// Create the UserContext with initial values
const UserContext = createContext<UserContextType>({
    darkMode: false,
    toggleDarkMode: () => { },
    setDarkMode: () => { }, // Provide a default empty function
    userSettings: { gender: '', demographic: '', beliefSystem: '' }, // Default user settings
    setUserSettings: () => { }, // Provide a default empty function
});

// Define UserProvider component
export const UserProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    // Define state for darkMode
    const [darkMode, setDarkMode] = useState<boolean>(false);

    // Define state for userSettings
    const [userSettings, setUserSettings] = useState<UserSettings>({
        gender: '',
        demographic: '',
        beliefSystem: ''
    });

    // Function to toggle dark mode
    const toggleDarkMode = () => {
        setDarkMode((prevMode) => !prevMode);
    };

    // Return UserContext.Provider with value and children
    return (
        <UserContext.Provider value={{ darkMode, toggleDarkMode, setDarkMode, userSettings, setUserSettings }}>
            {children}
        </UserContext.Provider>
    );
};

// Define useUserContext hook to use context in components
export const useUserContext = (): UserContextType => {
    const context = useContext(UserContext);
    if (!context) {
        throw new Error('useUserContext must be used within a UserProvider');
    }
    return context;
};