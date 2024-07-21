import React from 'react';
import './LanguageToggle.css'; // Ensure to import the CSS file

interface LanguageToggleProps {
    multilingual: boolean;
    setMultilingual: (value: boolean) => void;
}

const LanguageToggle: React.FC<LanguageToggleProps> = ({ multilingual, setMultilingual }) => {
    return (
        <div className="language-toggle">
            <input
                id="languageToggle"
                className="language-checkbox"
                type="checkbox"
                checked={multilingual}
                onChange={() => setMultilingual(!multilingual)}
            />
            <label htmlFor="languageToggle" className="language-label">
                <span className="english-icon">🇬🇧</span>
                <span className="multilingual-icon">🌐</span>
                <div className="toggle-ball"></div>
            </label>
        </div>
    );
}

export default LanguageToggle;
