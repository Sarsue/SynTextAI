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
                    <p className="tagline">Your Intelligent Research Partner</p>
                </div>
                <div className="auth-buttons">
                    <Link to="/login" className="signin-link">
                        <button className="primary-button">
                            Start Researching
                        </button>
                    </Link>
                </div>
            </header>

            {/* Hero Section */}
            <main className="content-container">
                <section className="hero-section">
                    <h2 className="hero-title">AI-Powered Research Assistant</h2>
                    <p className="hero-description">
                        Transform complex research into actionable insights. Perfect for consulting firms, 
                        analysts, and knowledge workers who need quick, reliable intelligence.
                    </p>
                    <Link to="/login" className="signin-link">
                        <button className="cta-button">Start Free Trial</button>
                    </Link>
                </section>

                {/* Features Section */}
                <section className="features-section">
                    <h2 className="section-title">Powerful Research Tools</h2>
                    <div className="features-grid">
                        <div className="feature-item">
                            <div className="feature-icon">📊</div>
                            <h3>Intelligent Summarization</h3>
                            <p>Automatically digest long reports and research papers into key insights and actionable points.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">🔍</div>
                            <h3>Research Synthesis</h3>
                            <p>Gather and analyze information from multiple sources for comprehensive research reports.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">🎯</div>
                            <h3>Competitive Intelligence</h3>
                            <p>Stay ahead with AI-powered market research and competitor analysis.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">📚</div>
                            <h3>Smart Explanations</h3>
                            <p>Get clear, context-aware explanations of complex topics and industry terminology.</p>
                        </div>
                    </div>
                </section>

                {/* Use Cases Section */}
                <section className="use-cases-section">
                    <h2 className="section-title">Perfect For</h2>
                    <div className="use-cases-grid">
                        <div className="use-case-item">
                            <h3>Consulting Firms</h3>
                            <p>Accelerate research and analysis for client projects</p>
                        </div>
                        <div className="use-case-item">
                            <h3>Business Analysts</h3>
                            <p>Quick market insights and competitor research</p>
                        </div>
                        <div className="use-case-item">
                            <h3>Knowledge Workers</h3>
                            <p>Efficient information gathering and synthesis</p>
                        </div>
                        <div className="use-case-item">
                            <h3>Students & Academics</h3>
                            <p>Research assistance with proper citations</p>
                        </div>
                    </div>
                </section>

                {/* Pricing Section */}
                <section className="pricing-section">
                    <h2 className="pricing-title">Professional Research Tools</h2>
                    <p className="pricing-description">
                        Start with a 30-day free trial. Then just <strong>$15/month</strong> for unlimited research assistance.
                        Perfect for professionals and teams.
                    </p>
                    <Link to="/login" className="signin-link">
                        <button className="cta-button">Begin Free Trial</button>
                    </Link>
                </section>
            </main>

            {/* Footer */}
            <footer className="footer">
                <p>&copy; 2025 OSAS INC. All rights reserved.</p>
                <p className="footer-info">Empowering professionals with AI-driven research tools.</p>
            </footer>
        </div>
    );
};

export default Home;
