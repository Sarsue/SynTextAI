import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import './Home.css';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';

const Home: React.FC = () => {
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
        window.location.href = '/login';
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
        handleGetStarted();
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
                    <Link to="/login" className="signup-button">Sign Up</Link>
                </div>
            </header>

            {/* Main Hero with Search */}
            <main className="consensus-main">
                <div className="hero-container">
                    <h1 className="hero-title">Learning starts here</h1>
                    <p className="hero-subtitle">Over 10,000 students and educators trust SynText AI</p>
                    
                    {/* Chat Interface similar to ChatApp */}
                    <div className="chat-container">
                        <div className="chat-messages">
                            <div className="chat-message ai">
                                <div className="message-avatar">AI</div>
                                <div className="message-content">
                                    <p>Hello! Upload a document or paste text, and I'll help you learn from it.</p>
                                </div>
                            </div>
                        </div>
                        
                        <form onSubmit={handleSubmit} className="chat-input-area">
                            <textarea 
                                className="chat-input" 
                                placeholder="Paste your text here or upload a document..."
                                value={inputText}
                                onChange={(e) => setInputText(e.target.value)}
                                rows={2}
                            />
                            
                            <div className="chat-actions">
                                <div className="file-upload">
                                    <label htmlFor="file-upload" className="file-upload-button">
                                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                            <polyline points="17 8 12 3 7 8"></polyline>
                                            <line x1="12" y1="3" x2="12" y2="15"></line>
                                        </svg>
                                        <span className="tooltip">{selectedFile ? selectedFile.name : "Upload document"}</span>
                                    </label>
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
                                >
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <line x1="22" y1="2" x2="11" y2="13"></line>
                                        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                                    </svg>
                                </button>
                            </div>
                        </form>
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