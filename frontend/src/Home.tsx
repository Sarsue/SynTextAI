import React from 'react';
import { Link } from 'react-router-dom';
import './Home.css';
import { useDarkMode } from './DarkModeContext';

const Home: React.FC = () => {
    const { darkMode } = useDarkMode();

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
                    <h2 className="hero-title">Discover Wisdom for Every Path – Spiritual, Secular, and Agnostic</h2>
                    <p className="hero-description">
                        Whether you seek guidance from ancient scriptures, modern philosophy, or mindfulness practices, SynText AI offers personalized insights to help you navigate life’s challenges.
                        <strong> Sign In and Start Your Journey Today </strong>
                    </p>

                </section>

                {/* Video and Features Section */}
                <section className="video-features-section">
                    <div className="features-column">
                        <h2 className="features-title">Why Choose SynText AI?</h2>
                        <ul className="features-list">
                            <li className="feature-item">
                                <i className="fas fa-question-circle feature-icon"></i>
                                <h3>Guidance on Life's Questions</h3>
                                <p>Ask questions on various topics in any language and receive insights from spiritual, secular, and agnostic perspectives.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-image feature-icon"></i>
                                <h3>Multimodal Interpretation</h3>
                                <p>Upload images, text etc  of multilingual texts for interpretation and understanding, enhancing your insights.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-calendar-alt feature-icon"></i>
                                <h3>Daily Prompts & Wisdom</h3>
                                <p>Receive daily tips, mantras, and "Wisdom of the Day," blending scripture and philosophical quotes based on your belief system.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-tools feature-icon"></i>
                                <h3>Growth Tools & Recommendations</h3>
                                <p>Access tools and resources for personal growth, including journal uploads and development recommendations.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-adjust feature-icon"></i>
                                <h3>Personalized Insights</h3>
                                <p>Select preferences for spiritual or secular insights tailored to your needs and beliefs.</p>
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
                    <h2 className="pricing-title">Start Your Self Improvement Journey</h2>
                    <p className="pricing-description">
                        For just <strong>$15/month</strong>.
                    </p>
                </section>
                <p>&copy; 2024 OSAS INC. All rights reserved.</p>
            </footer>
        </div>
    );
};

export default Home;
