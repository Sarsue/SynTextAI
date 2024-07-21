import React from 'react';
import './DarkModeToggle.css'; // Make sure to import the CSS file

interface DarkModeToggleProps {
    darkMode: boolean;
    setDarkMode: (value: boolean) => void;
}

const DarkModeToggle: React.FC<DarkModeToggleProps> = ({ darkMode, setDarkMode }) => {
    return (
        <div className="dark-mode-toggle">
            <input
                id="darkModeToggle"
                className="dark-mode-checkbox"
                type="checkbox"
                checked={darkMode}
                onChange={() => setDarkMode(!darkMode)}
            />
            <label htmlFor="darkModeToggle" className="dark-mode-label">
                <span className="sun-icon">☀️</span>
                <span className="moon-icon">🌙</span>
                <div className="toggle-ball"></div>
            </label>
        </div>
    );
}

export default DarkModeToggle;
