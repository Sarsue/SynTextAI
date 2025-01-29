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
                    <p className="tagline">Smarter Answers, Faster Insights</p>
                </div>
                <div className="auth-buttons">
                    <Link to="/login" className="signin-link">
                        <button className="google-sign-in-button">Sign in with Google</button>
                    </Link>
                </div>
            </header>

            {/* Main Content */}
            <main className="content-container">
                <section className="hero-section">
                    <h2 className="hero-title">AI-Powered Search Engine for Your Knowledge</h2>
                    <p className="hero-description">
                        SynText AI delivers real-time, accurate answers from your documents and the web—complete with citations.
                    </p>
                    <p className="hero-description">
                        Search across PDFs, research papers, and web sources effortlessly, with multilingual support and expanding compatibility.
                    </p>
                </section>
            </main>

            {/* Footer */}
            <footer className="footer">
                <p>&copy; 2025 OSAS INC. All rights reserved.</p>
                <p className="footer-info">Designed for professionals, researchers, and businesses looking for precise AI-driven insights.</p>
            </footer>
        </div>
    );
};

export default Home;