import React from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import './Home.css';
import { useUserContext } from './UserContext';

const Home: React.FC = () => {
    const { darkMode } = useUserContext();

    return (
        <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
            <Helmet>
                <title>SynText AI - Intelligent Text Analysis & Learning Tool</title>
                <meta name="description" content="Transform how you learn with SynText AI. Our platform analyzes documents, creates flashcards, generates quizzes, and extracts key concepts to enhance your learning experience." />
                <meta name="keywords" content="AI learning, document analysis, flashcards, quizzes, study tool, education technology, key concepts, learning assistant" />
                
                {/* Additional SEO optimization */}
                <link rel="canonical" href="https://syntextai.com/" />
                <script type="application/ld+json">
                    {
                        JSON.stringify({
                            "@context": "https://schema.org",
                            "@type": "WebApplication",
                            "name": "SynText AI",
                            "description": "Transform how you learn with SynText AI. Our platform analyzes documents, creates flashcards, generates quizzes, and extracts key concepts to enhance your learning experience.",
                            "applicationCategory": "EducationalApplication",
                            "offers": {
                                "@type": "Offer",
                                "price": "0",
                                "priceCurrency": "USD"
                            }
                        })
                    }
                </script>
            </Helmet>
            {/* Header */}
            <header className="header">
                <div className="logo-container">
                    <h1 className="app-title">SynText AI</h1>
                    <p className="tagline">Your AI Learning Companion</p>
                </div>
                <div className="auth-buttons">
                    <Link to="/login" className="signin-link">
                        <button className="primary-button">
                            Start Learning
                        </button>
                    </Link>
                </div>
            </header>

            {/* Hero Section */}
            <main className="content-container">
                <section className="hero-section">
                    <h1 className="hero-title">AI-Powered Learning Assistant</h1>
                    <p className="hero-description">
                        Transform complex educational content into easy-to-understand key concepts. Perfect for students,
                        lifelong learners, and educators who want to accelerate understanding and retention.
                    </p>
                    <Link to="/login" className="signin-link">
                        <button className="cta-button">Start Free Trial</button>
                    </Link>
                </section>

                {/* Features Section */}
                <section className="features-section">
                    <h2 className="section-title">Powerful Learning Tools</h2>
                    <div className="features-grid">
                        <div className="feature-item">
                            <div className="feature-icon">📄</div>
                            <h3>PDF Document Analysis</h3>
                            <p>Upload PDF textbooks, papers, and documents to extract key concepts and explanations with page references.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">📺</div>
                            <h3>YouTube Video Learning</h3>
                            <p>Process educational YouTube videos and get key concepts with precise timestamp links for review.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">🔑</div>
                            <h3>Key Concept Extraction</h3>
                            <p>Automatically identify and explain the most important concepts from any learning material.</p>
                        </div>
                        <div className="feature-item">
                            <div className="feature-icon">📚</div>
                            <h3>Interactive Learning</h3>
                            <p>Chat with your content, ask questions, and get tailored explanations based on your comprehension level.</p>
                        </div>
                    </div>
                </section>

                {/* Use Cases Section */}
                <section className="use-cases-section">
                    <h2 className="section-title">Perfect For</h2>
                    <div className="use-cases-grid">
                        <div className="use-case-item">
                            <h3>College Students</h3>
                            <p>Master course materials faster with key concept extraction</p>
                        </div>
                        <div className="use-case-item">
                            <h3>Self-Learners</h3>
                            <p>Process educational content and retain information better</p>
                        </div>
                        <div className="use-case-item">
                            <h3>Educators</h3>
                            <p>Create focused learning materials from complex sources</p>
                        </div>
                        <div className="use-case-item">
                            <h3>Lifelong Learners</h3>
                            <p>Learn from videos and documents with structured guidance</p>
                        </div>
                    </div>
                </section>

                {/* Pricing Section */}
                <section className="pricing-section">
                    <h2 className="pricing-title">Accelerate Your Learning</h2>
                    <p className="pricing-description">
                        Start with a 30-day free trial. Then just <strong>$15/month</strong> for unlimited access to all learning tools.
                        Perfect for students and continuous learners.
                    </p>
                    <Link to="/login" className="signin-link">
                        <button className="cta-button">Begin Free Trial</button>
                    </Link>
                </section>
            </main>

            {/* Footer */}
            <footer className="footer">
                <p>&copy; 2025 OSAS INC. All rights reserved.</p>
                <p className="footer-info">Empowering learners with AI-driven concept extraction and explanations.</p>
            </footer>
        </div>
    );
};

export default Home;
