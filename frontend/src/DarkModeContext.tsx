import React, { createContext, useContext, useState } from 'react';

// Define the type for DarkModeContext
interface DarkModeContextType {
    darkMode: boolean;
    toggleDarkMode: () => void;
    setDarkMode: (darkMode: boolean) => void; // Add setDarkMode function
}

// Create the DarkModeContext with initial values
const DarkModeContext = createContext<DarkModeContextType>({
    darkMode: true,
    toggleDarkMode: () => { },
    setDarkMode: () => { }, // Provide a default empty function
});

// Define DarkModeProvider component
export const DarkModeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    // Define state for darkMode
    const [darkMode, setDarkMode] = useState<boolean>(true);

    // Function to toggle dark mode
    const toggleDarkMode = () => {
        setDarkMode((prevMode) => !prevMode);
    };

    // Return DarkModeContext.Provider with value and children
    return (
        <DarkModeContext.Provider value={{ darkMode, toggleDarkMode, setDarkMode }}>
            {children}
        </DarkModeContext.Provider>
    );
};

// Define useDarkMode hook to use context in components
export const useDarkMode = (): DarkModeContextType => useContext(DarkModeContext);
