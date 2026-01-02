import React from 'react';
import './UpgradePrompt.css';

interface UpgradePromptProps {
    title: string;
    message: string;
    limitType: 'docs' | 'storage' | 'workspace' | 'general';
    onClose: () => void;
    darkMode?: boolean;
}

const UpgradePrompt: React.FC<UpgradePromptProps> = ({ 
    title, 
    message, 
    limitType, 
    onClose, 
    darkMode = false 
}) => {
    const getIcon = () => {
        switch (limitType) {
            case 'docs': return '📄';
            case 'storage': return '💾';
            case 'workspace': return '📁';
            default: return '⚠️';
        }
    };

    const getFeatures = () => {
        return [
            '📚 Unlimited documents',
            '💾 Unlimited storage',
            '📁 Multiple workspaces',
            '🔍 Advanced search',
            '⚡ Priority processing',
            '🎯 Premium support'
        ];
    };

    return (
        <div className="upgrade-prompt-overlay" onClick={onClose}>
            <div 
                className={`upgrade-prompt-modal ${darkMode ? 'dark-mode' : ''}`}
                onClick={(e) => e.stopPropagation()}
            >
                <button 
                    className="close-btn"
                    onClick={onClose}
                    aria-label="Close"
                >
                    ×
                </button>

                <div className="prompt-icon">{getIcon()}</div>
                
                <h2 className="prompt-title">{title}</h2>
                <p className="prompt-message">{message}</p>

                <div className="features-section">
                    <h3>Upgrade to Premium</h3>
                    <ul className="features-list">
                        {getFeatures().map((feature, index) => (
                            <li key={index}>{feature}</li>
                        ))}
                    </ul>
                </div>

                <div className="pricing-preview">
                    <div className="price">
                        <span className="currency">$</span>
                        <span className="amount">9</span>
                        <span className="period">/month</span>
                    </div>
                    <p className="trial-info">✨ 7-day free trial included</p>
                </div>

                <div className="action-buttons">
                    <button className="dismiss-btn" onClick={onClose}>
                        Not Now
                    </button>
                    <a href="/settings" className="upgrade-btn">
                        Upgrade Now
                    </a>
                </div>
            </div>
        </div>
    );
};

export default UpgradePrompt;
