import React from 'react';
import { Link } from 'react-router-dom';
import './Home.css';
import { useDarkMode } from './DarkModeContext';


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
                    <p className="features-intro"> AI for Documents - Translate and Summarize  documents and answer questions.</p>
                    <div className="features-list">
                        <div className="feature-item">
                            <h3>Multiple Document Support</h3>
                            <p> SyntextAI works on PDF, CSV, Txt, Image and more coming.</p>
                        </div>
                        <div className="feature-item">
                            <h3>Data Management</h3>
                            <p>Export your chat's and delete your files</p>
                        </div>
                        <div className="feature-item">
                            <h3>Cited Sources</h3>
                            <p>Answers contain references to their source in the original document.</p>
                        </div>
                        <div className="feature-item">
                            <h3>Any Language</h3>
                            <p>Works worldwide! SynTextAI accepts documents in any language and can chat in any language.</p>
                        </div>
                    </div>
                </section>

                <div className="pricing-table">
                    <div className="pricing-card">
                        <h2 className="pricing-title">14 Days Trial</h2>
                        <p className="pricing-info">Then Pay $15 Monthly You'll Know Its Worth It</p>
                        {/* <ul className="pricing-features">
                            <li>Multilingual (English, French, Italian, German, Spanish and Code) QA on Documents</li>
                            <li>Document Summarization</li>
                            <li>Document Translation</li>
                        </ul> */}
                        {/* Removed Signup button */
                            // <BuyButtonComponent
                            //     buyButtonId="buy_btn_1PmFYiHuDDTkwuzjuYQGB6IQ"
                            //     publishableKey="pk_live_51OXYPHHuDDTkwuzjvUVcNwur0xLQx7UWYfMN6d8hjbHUMhYlu7IJx0qEGyvZQhbIGSRKDxhCuXk6e1rQgnSh5XXu004fNj9Pwj"
                            // />
                        }
                    </div>
                </div>
            </div>
            <div className="company-logo-container">
                {/* <img src="/company-logo.png" alt="Company Logo" style={logoStyle} /> */}
                <p>Developed By OSAS INC.</p>

            </div>
        </div>
    );
};

export default Home;
