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

            {/* Main Content */}
            <main className="content-container">
                {/* Hero Section */}
                <section className="hero-section">
                    <h2 className="hero-title">AI-Powered Insights, Anytime, Anywhere</h2>
                    <p className="hero-description">
                        SynText AI transforms how professionals learn, analyze, and communicate. Extract insights, summarize, and translate knowledge from documents, web pages, and multimedia—all in one platform.
                        <strong> Elevate your expertise with SynText AI today.</strong>
                    </p>
                    {/* <Link to="/signup">
                        <button className="cta-button">Get Started Now</button>
                    </Link> */}
                </section>

                {/* Features Section */}
                <section className="features-section">
                    <h2 className="features-title">Why Professionals Choose SynText AI</h2>
                    <ul className="features-list">
                        <li className="feature-item">
                            <h3>Rapid Knowledge Discovery</h3>
                            <p>Analyze web pages, documents, and files in seconds.</p>
                        </li>
                        <li className="feature-item">
                            <h3>Multilingual Capabilities</h3>
                            <p>Engage seamlessly with content in multiple languages.</p>
                        </li>
                        <li className="feature-item">
                            <h3>Comprehensive Formats</h3>
                            <p>Interact with content in text and audio formats effortlessly.</p>
                        </li>
                        <li className="feature-item">
                            <h3>Actionable Results</h3>
                            <p>Summarize, extract, and share key insights instantly.</p>
                        </li>
                        <li className="feature-item">
                            <h3>Customizable Experience</h3>
                            <p>Adjust settings like dark mode to fit your workflow.</p>
                        </li>
                    </ul>
                </section>

                {/* Demo Section */}
                <section className="demo-section">
                    <h2 className="demo-title">See SynText AI in Action</h2>
                    <div className="video-container">
                        <iframe
                            width="100%"
                            height="300"
                            src="https://www.youtube.com/embed/your-video-id"
                            title="SynText AI Demo"
                            frameBorder="0"
                            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                            allowFullScreen
                        ></iframe>
                    </div>
                </section>
            </main>

            {/* Footer */}
            <footer className="footer">
                <section className="pricing-section">
                    <h2 className="pricing-title">Start Your Journey with SynText AI</h2>
                    <p className="pricing-description">
                        Access the full power of SynText AI for <strong>just $15/month</strong>. Cancel anytime.
                    </p>
                    {/* <Link to="/signup">
                        <button className="pricing-button">Subscribe Now</button>
                    </Link> */}
                </section>
                <p>&copy; 2024 OSAS INC. All rights reserved.</p>
            </footer>
        </div>
    );
};

export default Home;
