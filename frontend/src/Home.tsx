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
                    <h2 className="hero-title">Find Guidance Through Scripture and Manage Your Documents</h2>
                    <p className="hero-description">
                        SynText AI offers spiritual guidance, powered by ancient wisdom like the Bible, and simplifies document management with AI-driven features.
                    </p>
                </section>

                {/* Video and Features Side by Side */}
                <section className="video-features-section">
                    <div className="features-column">
                        <h2 className="features-title">Why Choose SynText AI?</h2>
                        <ul className="features-list">
                            <li className="feature-item">
                                <i className="fas fa-book feature-icon"></i>
                                <h3>Bible-Based Guidance</h3>
                                <p>Receive instant advice grounded in biblical wisdom for your spiritual and personal challenges.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-language feature-icon"></i>
                                <h3>Multilingual Support</h3>
                                <p>Ask questions and get answers in multiple languages, with support for scripture in different translations.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-search feature-icon"></i>
                                <h3>Share With Social Media</h3>
                                <p>Generate blog posts, social media updates, or even Bible-based reflections with AI.</p>
                            </li>
                        </ul>
                    </div>

                    <div className="video-column">
                        <h2 className="video-title">See SynText AI in Action</h2>
                        <div className="video-container">
                            <iframe
                                width="100%"
                                height="250"
                                src="https://www.youtube.com/embed/your-video-id"
                                title="SynText AI Demo"
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
