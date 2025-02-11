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
                    <p className="tagline">Empowering Insights, Instantly</p>
                </div>
                <div className="auth-buttons">
                    <Link to="/login" className="signin-link">
                        <button className="primary-button">
                            Start Free Trial
                        </button>
                    </Link>
                </div>
            </header>

            {/* Hero Section */}
            <main className="content-container">
                <section className="hero-section">
                    <h2 className="hero-title">Your Ultimate AI Knowledge Assistant</h2>
                    <p className="hero-description">
                        Instantly search, summarize, and extract insights from PDFs, research papers, and web sources with verified citations.
                    </p>
                    <p className="hero-description">
                        Experience multilingual support and evolving document compatibility.
                    </p>
                    <Link to="/login" className="signin-link">
                        <button className="cta-button">Try It Free for 30 Days</button>
                    </Link>
                </section>

                {/* Features Section */}
                <section className="features-section">
                    <h2 className="section-title">Why Choose SynText AI?</h2>
                    <div className="features-grid">
                        <div className="feature-item">
                            <div className="feature-icon">🔍</div>
                            <h3>AI-Powered Search</h3>
                            <p>Find precise answers from PDFs, books, and research instantly.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">📖</div>
                            <h3>Verified Citations</h3>
                            <p>Every answer includes source references for credibility.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">🌍</div>
                            <h3>Multilingual Support</h3>
                            <p>Seamless understanding in multiple languages.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">🕒</div>
                            <h3>Time-Saving</h3>
                            <p>Reduce research time and enhance productivity.</p>
                        </div>
                    </div>
                </section>

                {/* Embedded Video */}
                <section className="video-section">
                    <div className="video-container">
                        <iframe
                            width="560"
                            height="315"
                            src="https://www.youtube.com/embed/4oy5PdsxI4E"
                            title="SynText AI Demo"
                            frameBorder="0"
                            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                            allowFullScreen
                        ></iframe>
                    </div>
                </section>

                {/* Testimonials Section
                <section className="testimonials-section">
                    <h2 className="section-title">What Our Users Say</h2>
                    <div className="testimonial-grid">
                        <div className="testimonial-item">
                            <p>"SynText AI has revolutionized my research process!" - Helen </p>
                        </div>
                        <div className="testimonial-item">
                            <p>"I like the export chat history feature" - Andre Blaise </p>
                        </div>
                    </div>
                </section> */}

                {/* Pricing Section */}
                <section className="pricing-section">
                    <h2 className="pricing-title">Start Your Free Trial Today</h2>
                    <p className="pricing-description">
                        Enjoy full access to all features for 30 days—no credit card required.
                        After that, unlimited AI searches for just <strong>$15/month</strong>.
                    </p>
                    <Link to="/login" className="signin-link">
                        <button className="cta-button">Start Free Trial</button>
                    </Link>
                </section>
            </main>

            {/* Footer */}
            <footer className="footer">
                <p>&copy; 2025 OSAS INC. All rights reserved.</p>
                <p className="footer-info">Designed for professionals, researchers, and businesses seeking AI-driven insights.</p>
            </footer>
        </div>
    );
};

export default Home;
