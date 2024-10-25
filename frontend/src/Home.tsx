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
                    <h2 className="hero-title">Unlock Knowledge, Transform Your Understanding</h2>
                    <p className="hero-description">
                        With SynText AI, easily summarize and explain complex concepts, translate text into multiple languages, and retrieve relevant documents based on your queries.
                        <strong> Sign In and Enhance Your Learning Today!</strong>
                    </p>
                </section>

                {/* Video and Features Section */}
                <section className="video-features-section">
                    <div className="features-column">
                        <h2 className="features-title">Why Choose SynText AI?</h2>
                        <ul className="features-list">
                            <li className="feature-item">
                                <i className="fas fa-lightbulb feature-icon"></i>
                                <h3>Concept Summarization</h3>
                                <p>Effortlessly summarize complex ideas and concepts into clear, concise explanations tailored to your understanding.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-language feature-icon"></i>
                                <h3>Multilingual Translation</h3>
                                <p>Translate documents and texts into various languages, making information accessible to everyone.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-search feature-icon"></i>
                                <h3>Relevant Document Retrieval</h3>
                                <p>Retrieve pertinent documents and resources based on your queries, enhancing your research capabilities.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-tools feature-icon"></i>
                                <h3>Growth Tools & Resources</h3>
                                <p>Access a range of tools for personal and professional growth, including insights tailored to your learning journey.</p>
                            </li>
                            <li className="feature-item">
                                <i className="fas fa-user-circle feature-icon"></i>
                                <h3>Personalized Insights</h3>
                                <p>Receive insights based on your interests and preferences, designed to enrich your understanding and knowledge base.</p>
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
