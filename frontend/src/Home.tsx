import React from 'react';
import { Link } from 'react-router-dom';
import './Home.css';
import { useDarkMode } from './DarkModeContext';
import BuyButtonComponent from './components/BuyButtonComponent';

const Home: React.FC = () => {
    const { darkMode } = useDarkMode();
    const logoStyle = {
        maxWidth: '100px', // Adjust as needed
        height: 'auto',
    };

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

                <section className="features-section">
                    <h2 className="features-title">SynTextAI in a Nutshell</h2>
                    <p className="features-intro"> AI for Documents - . Translate, Summarize and answer questions.</p>
                    <div className="features-list">
                        <div className="feature-item">
                            <h3>Multiple Document Support</h3>
                            <p> SyntextAI works on PDF, CSV, Txt, Image and support for many coming.</p>
                        </div>
                        <div className="feature-item">
                            <h3>Data Management Chats</h3>
                            <p>Manage your chats and documents, you can export and delete your data.</p>
                        </div>
                        <div className="feature-item">
                            <h3>Cited Sources</h3>
                            <p>Answers contain references to their source in the original document. No Hallucinations.</p>
                        </div>
                        <div className="feature-item">
                            <h3>Any Language</h3>
                            <p>Works worldwide! SynTextAI accepts documents in any language and can chat in any language.</p>
                        </div>
                    </div>
                </section>

                <div className="pricing-table">
                    <div className="pricing-card">
                        <h2 className="pricing-title">Monthly Plan</h2>
                        <p className="pricing-price">$20</p>
                        <p className="pricing-info">First month free on sign-up</p>
                        {/* <ul className="pricing-features">
                            <li>Multilingual (English, French, Italian, German, Spanish and Code) QA on Documents</li>
                            <li>Document Summarization</li>
                            <li>Document Translation</li>
                        </ul> */}
                        {/* Removed Signup button */}
                    </div>
                </div>
            </div>
            <div className="company-logo-container">
                <img src="/company-logo.png" alt="Company Logo" style={logoStyle} />
            </div>
        </div>
    );
};

export default Home;
