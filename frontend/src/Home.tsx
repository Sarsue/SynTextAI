import React from 'react';
import { Link } from 'react-router-dom';
import './Home.css';
import { useDarkMode } from './DarkModeContext';

const Home: React.FC = () => {
    const { darkMode } = useDarkMode();

    return (
        <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
            <header className="header">
                <div className="logo-container">
                    <h1 className="app-title">SynText AI</h1>
                </div>
                <div className="auth-buttons">
                    <Link to="/login" className="signin-link">
                        <button className="google-sign-in-button">Sign in with Google</button>
                    </Link>
                </div>
            </header>

            <main className="content-container">
                <section className="hero-section">
                    <h2 className="hero-title">Manage Your Documents Like Never Before</h2>
                    <p className="hero-description">
                        Unlock the power of SynText: Effortlessly manage your documents with advanced multilingual QA, intelligent summarization, and seamless document generation.
                    </p>
                </section>

                <section className="features-section">
                    <h2 className="features-title">SynTextAI Features</h2>
                    <div className="features-list">
                        <div className="feature-item">
                            <i className="fas fa-file-alt feature-icon"></i>
                            <h3>Multiple Document Support</h3>
                            <p>SynTextAI works on PDF, CSV, Txt, and Images, with more formats coming soon.</p>
                        </div>
                        <div className="feature-item">
                            <i className="fas fa-tasks feature-icon"></i>
                            <h3>Data Management</h3>
                            <p>Export your chats and delete files effortlessly.</p>
                        </div>
                        <div className="feature-item">
                            <i className="fas fa-quote-right feature-icon"></i>
                            <h3>Cited Sources</h3>
                            <p>Get answers with references to their sources in your original documents.</p>
                        </div>
                        <div className="feature-item">
                            <i className="fas fa-language feature-icon"></i>
                            <h3>Language Flexibility</h3>
                            <p>Works worldwide! SynTextAI accepts and translates documents in any language.</p>
                        </div>
                    </div>
                </section>

                <section className="pricing-section">
                    <div className="pricing-card">
                        <h2 className="pricing-title">No Free Trial</h2>
                        <p className="pricing-info">Pay $15/month, and cancel if it's not worth it!</p>
                        {/* <Link to="/pricing" className="pricing-button">Start Your Trial</Link> */}
                    </div>
                </section>
            </main>

            <footer className="footer">
                <p>&copy; 2024 OSAS INC. All rights reserved.</p>
            </footer>
        </div>
    );
};

export default Home;
