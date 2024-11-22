import React from 'react';
import { Link } from 'react-router-dom';
import './Home.css';
import { useUserContext } from './UserContext';

const Home: React.FC = () => {
    const { darkMode } = useUserContext();

    return (
        <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
            {/* Header */}
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
                    <h2 className="hero-title">AI-Powered Insights for Your Documents</h2>
                    <p className="hero-description">
                        Transform how you interact with information. Summarize, translate, and extract insights from your files in seconds. <strong>Start your knowledge journey today with SynText AI!</strong>
                    </p>
                </section>

                {/* Video and Features Section */}
                <section className="video-features-section">
                    <div className="features-column">
                        <h2 className="features-title">What SynText AI Can Do for You</h2>
                        <ul className="features-list">
                            <li className="feature-item">
                                <i className="fas fa-signal feature-icon"></i>
                                <h3>Automated Workflows</h3>
                                <p>Auto-summarize and highlight key takeaways from documents instantly upon upload.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-language feature-icon"></i>
                                <h3>Multilingual Support</h3>
                                <p>Translate and process documents in multiple languages seamlessly.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-user-friends feature-icon"></i>
                                <h3>Collaborative Sharing</h3>
                                <p>Share insights, summaries, and conversations directly with your team or external tools like Slack and email.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-cog feature-icon"></i>
                                <h3>Customizable Experience</h3>
                                <p>Dark mode, adjustable font sizes, and more for a user-friendly interface.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-microphone feature-icon"></i>
                                <h3>Voice Interaction</h3>
                                <p>Enjoy hands-free interaction with text-to-speech and voice-to-text features.</p>
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

            {/* Footer */}
            <footer className="footer">
                {/* Pricing Section */}
                <section className="pricing-section">
                    <h2 className="pricing-title">Start Your Knowledge Journey</h2>
                    <p className="pricing-description">
                        For just <strong>$15/month</strong>, unlock the full potential of SynText AI.
                    </p>
                </section>
                <p>&copy; 2024 OSAS INC. All rights reserved.</p>
            </footer>
        </div>
    );
};

export default Home;
