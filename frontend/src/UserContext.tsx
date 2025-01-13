import React, { createContext, useContext, useState, ReactNode } from 'react';

// Define the type for UserSettings
interface UserSettings {
    comprehensionLevel: string; // Added comprehensionLevel
    selectedLanguage: string;    // Added selectedLanguage
}

// Define the type for UserContext
interface UserContextType {
    darkMode: boolean;
    toggleDarkMode: () => void;
    setDarkMode: (darkMode: boolean) => void; // Add setDarkMode function
    userSettings: UserSettings; // Add userSettings to context type
    setUserSettings: (settings: UserSettings) => void; // Add setUserSettings function
    isPollingFiles: boolean; // Add isPollingFiles state
    setIsPollingFiles: (isPollingFile: boolean) => void; // Add setIsPollingFiles function
    isPollingMessages: boolean; // Add isPollingFiles state
    setIsPollingMessages: (isPollingMsg: boolean) => void; // Add setIsPollingFiles function
}

// Create the UserContext with initial values
const UserContext = createContext<UserContextType>({
    darkMode: false,
    toggleDarkMode: () => { },
    setDarkMode: () => { }, // Provide a default empty function
    userSettings: {
        comprehensionLevel: '', // Default comprehensionLevel
        selectedLanguage: '',    // Default selectedLanguage
    },
    setUserSettings: () => { }, // Provide a default empty function
    isPollingFiles: false, // Default poling file state
    setIsPollingFiles: () => { },
    isPollingMessages: false, // Default polling message state
    setIsPollingMessages: () => { },
});

// Define UserProvider component
export const UserProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    // Define state for darkMode
    const [darkMode, setDarkMode] = useState<boolean>(false);

    // Define state for userSettings
    const [userSettings, setUserSettings] = useState<UserSettings>({
        comprehensionLevel: '', // Initial value for comprehensionLevel
        selectedLanguage: '',    // Initial value for selectedLanguage
    });

    const [isPollingFiles, setIsPollingFiles] = useState<boolean>(false); // Add poling files state
    const [isPollingMessages, setIsPollingMessages] = useState<boolean>(false); // Add poling messages state

    // Function to toggle dark mode
    const toggleDarkMode = () => {
        setDarkMode((prevMode) => !prevMode);
    };

    // Return UserContext.Provider with value and children
    return (
        <UserContext.Provider value={{ darkMode, toggleDarkMode, setDarkMode, userSettings, setUserSettings, isPollingFiles, setIsPollingFiles, isPollingMessages, setIsPollingMessages }}>
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
