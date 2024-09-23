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
                {/* Hero Section */}
                <section className="hero-section">
                    <h2 className="hero-title">Manage Your Documents Like Never Before</h2>
                    <p className="hero-description">
                        Unlock the power of SynText: Effortlessly manage, analyze, and simplify your documents with AI-driven features.
                    </p>
                </section>

                {/* Video and Features Side by Side */}
                <section className="video-features-section">
                    <div className="features-column">
                        <h2 className="features-title">Why SynTextAI?</h2>
                        <ul className="features-list">
                            <li className="feature-item">
                                <i className="fas fa-file-alt feature-icon"></i>
                                <h3>Multiple Document Support</h3>
                                <p>Works on PDFs, CSVs, Text, and Images.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-tasks feature-icon"></i>
                                <h3>Data Management</h3>
                                <p>Easily manage, export, and delete your files.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-quote-right feature-icon"></i>
                                <h3>Cited Sources</h3>
                                <p>Get answers with references to their sources in your documents.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-language feature-icon"></i>
                                <h3>Multilingual Support</h3>
                                <p>queries and documents in multiple languages.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-search feature-icon"></i>
                                <h3>AI-Powered Capabilities</h3>
                                <p>Search, summarize, simplify, and generate content with AI.</p>
                            </li>
                        </ul>
                    </div>

                    <div className="video-column">
                        <h2 className="video-title">Watch SynTextAI in Action</h2>
                        <div className="video-container">
                            <iframe
                                width="100%"
                                height="250"
                                src="https://www.youtube.com/embed/your-video-id"
                                title="SynText AI Tutorial"
                                frameBorder="0"
                                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                                allowFullScreen
                            ></iframe>
                        </div>
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
