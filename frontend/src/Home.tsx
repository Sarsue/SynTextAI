import React from 'react';
import { Link } from 'react-router-dom';
import './Home.css';
import { useDarkMode } from './DarkModeContext';
import BuyButtonComponent from './components/BuyButtonComponent';

const Home: React.FC = () => {
    const { darkMode } = useDarkMode();

    return (
        <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
            <div className={`content-container ${darkMode ? 'dark-mode' : ''}`}>
                <h1 className="app-title">SynText AI</h1>
                <p className="app-description">
                    Unlock the power of SynText: Effortlessly manage your documents with advanced multilingual QA, intelligent summarization, and seamless translation.
                </p>
                <Link to="/login" className="signin-link">
                    <button className="google-sign-in-button">Sign in with Google</button>
                </Link>
                <div className="pricing-table">
                    <div className="pricing-card">
                        <h2 className="pricing-title">Monthly Plan</h2>
                        <p className="pricing-price">$20</p>
                        <p className="pricing-info">First month free on sign-up</p>
                        <ul className="pricing-features">
                            <li>Multilingual (English, French, Italian, German, Spanish and Code) QA on Documents</li>
                            <li>Document Summarization</li>
                            <li>Document Translation</li>
                        </ul>
                        {/* Removed Signup button */}
                    </div>
                </div>
            </div>
            <div className="company-logo-container">
                <img src="/company-logo.png" alt="Company Logo" />
            </div>
        </div>
    );
};

export default Home;
