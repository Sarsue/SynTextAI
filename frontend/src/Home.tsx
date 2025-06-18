import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import './Home.css';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';

const Home: React.FC = () => {
    const navigate = useNavigate();
    const { darkMode } = useUserContext();
    const posthog = usePostHog();
    const [inputText, setInputText] = useState('');
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    
    const handleGetStarted = () => {
        posthog.capture('homepage_get_started', {
            source: 'demo_section',
            has_input: inputText.length > 0,
            has_file: selectedFile !== null
        });
        navigate('/login');
    };
    
    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files[0]) {
            setSelectedFile(e.target.files[0]);
            posthog.capture('homepage_file_selected', {
                file_type: e.target.files[0].type,
                file_size: e.target.files[0].size
            });
        }
    };
    
    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        
        // Track the attempt to send a message
        posthog.capture('homepage_message_attempt', {
            has_input: inputText.length > 0,
            has_file: selectedFile !== null
        });
        
        // Redirect to login
        navigate('/login');
    };
    
    // Handler to redirect to login
    const redirectToLogin = () => {
        posthog.capture('homepage_interaction');
        navigate('/login');
    };
    
    // Core features to showcase
    const features = [
        { 
            name: "Create Flashcards", 
            icon: "🗂️", 
            description: "Automatically generate study flashcards from your documents"
        },
        { 
            name: "Extract Key Concepts", 
            icon: "🔑", 
            description: "Identify and understand the most important ideas in any text"
        },
        { 
            name: "Self-Assessment Quizzes", 
            icon: "📝", 
            description: "Test your knowledge with AI-generated quizzes tailored to your content"
        },
        { 
            name: "Search Information", 
            icon: "🔍", 
            description: "Find specific details within your documents instantly"
        }
    ];

    return (
        <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
            <Helmet>
                <title>SynText AI - Intelligent Text Analysis & Learning Tool</title>
                <meta name="description" content="Transform how you learn with SynText AI. Our platform analyzes documents, creates flashcards, generates quizzes, and extracts key concepts to enhance your learning experience." />
                <meta name="keywords" content="AI learning, document analysis, flashcards, quizzes, study tool, education technology, key concepts, learning assistant" />
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
            
            {/* Minimal Header */}
            <header className="consensus-header">
                <div className="logo-container">
                    <h1 className="app-title">SynText AI</h1>
                </div>
                <div className="auth-buttons">
                    <Link to="/login" className="signin-link">Sign In</Link>
                    <Link 
                        to="/login" 
                        className="signup-button"
                        onClick={() => {
                            posthog.capture('homepage_signup_click');
                            localStorage.setItem('authIntent', 'signup');
                        }}
                    >
                        Sign Up
                    </Link>
                </div>
            </header>

            {/* Main Hero with Search */}
            <main className="consensus-main">
                <div className="hero-section">
                    <div className="hero-content">
                        <h2 className="hero-title">Unlock Knowledge from Any Document</h2>
                        <p className="hero-text">SynText AI analyzes your documents, generates flashcards, creates quizzes, and extracts key concepts to enhance your learning and comprehension.</p>
                    </div>
                    
                    <div className="home-input-preview">
                        
                        <div className="chat-container">
                            <h2 className="input-preview-title">Analyze any document instantly</h2>
                            <p className="input-preview-description">Upload a PDF, paste text, or enter a YouTube link to get summaries, flashcards, quizzes, and more.</p>
                            
                            <form onSubmit={handleSubmit} className="chat-input-container">
                                <textarea 
                                    className="chat-input" 
                                    placeholder="Paste your text here or upload a document..."
                                    value={inputText}
                                    onChange={(e) => setInputText(e.target.value)}
                                    rows={3}
                                />
                                
                                <div className="chat-actions">
                                    <div className="file-upload-wrapper">
                                        <span 
                                            className="add-content-button"
                                            aria-label="Add content"
                                            onClick={() => {document.getElementById('file-upload')?.click()}}
                                        >
                                            ➕
                                        </span>
                                        <input 
                                            id="file-upload"
                                            type="file" 
                                            onChange={handleFileSelect} 
                                            style={{display: 'none'}}
                                            accept=".pdf,.doc,.docx,.txt"
                                        />
                                    </div>
                                    
                                    <button 
                                        type="submit" 
                                        className="send-button"
                                        onClick={(e) => {
                                            e.preventDefault();
                                            redirectToLogin();
                                        }}
                                        aria-label="Send message"
                                    >
                                        ✉️
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>

                </div>
                
                {/* Features Section */}
                <div className="features-section">
                    <h2 className="features-title">Transform How You Learn</h2>
                    <div className="features-grid">
                        {features.map((feature, index) => (
                            <div className="feature-item" key={index}>
                                <div className="feature-icon">{feature.icon}</div>
                                <h3 className="feature-title">{feature.name}</h3>
                                <p className="feature-description">{feature.description}</p>
                            </div>
                        ))}
                    </div>
                </div>
                

                {/* Simple CTA */}
                <div className="pricing-cta-section">
                    <h2 className="pricing-title">Ready to enhance your learning?</h2>
                    <p className="pricing-description">Get started with SynText AI today</p>
                    <Link 
                        to="/login" 
                        className="pricing-button"
                        onClick={() => posthog.capture('cta_clicked')}
                    >
                        Try SynText AI
                    </Link>
                </div>
            </main>

            {/* Simplified Footer */}
            <footer className="consensus-footer">
                <div className="copyright">
                    <p>© 2025 OSAS INC. All rights reserved.</p>
                </div>
            </footer>
        </div>
    );
};

export default Home;