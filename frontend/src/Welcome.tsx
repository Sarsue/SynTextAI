import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';
import './Welcome.css';

const Welcome: React.FC = () => {
    const navigate = useNavigate();
    const { user, darkMode } = useUserContext();
    const [step, setStep] = useState(1);
    const posthog = usePostHog();
    const totalSteps = 3;
    
    useEffect(() => {
        // If no user is authenticated, redirect to home
        if (!user) {
            navigate('/');
        }
        
        // Track onboarding start
        posthog.capture('onboarding_started');
    }, [user, navigate, posthog]);
    
    const handleNext = () => {
        if (step < totalSteps) {
            setStep(step + 1);
            posthog.capture('onboarding_step', { step: step + 1 });
        } else {
            // Complete onboarding
            posthog.capture('onboarding_completed');
            navigate('/chat');
        }
    };
    
    const handleSkip = () => {
        posthog.capture('onboarding_skipped', { from_step: step });
        navigate('/chat');
    };
    
    return (
        <div className={`welcome-container ${darkMode ? 'dark-mode' : ''}`}>
            <div className="welcome-card">
                <div className="welcome-header">
                    <h1>Welcome to SynText AI!</h1>
                    <div className="steps-indicator">
                        {Array.from({ length: totalSteps }).map((_, idx) => (
                            <div 
                                key={idx} 
                                className={`step-dot ${idx + 1 <= step ? 'active' : ''}`}
                            ></div>
                        ))}
                    </div>
                </div>
                
                <div className="welcome-content">
                    {step === 1 && (
                        <div className="welcome-step">
                            <div className="welcome-icon">👋</div>
                            <h2>Welcome, {user?.displayName?.split(' ')[0] || 'there'}!</h2>
                            <p>
                                Thanks for joining SynText AI. We're excited to help you unlock knowledge
                                from your documents and learn more effectively.
                            </p>
                            <p>Let's get you set up in just a few quick steps.</p>
                        </div>
                    )}
                    
                    {step === 2 && (
                        <div className="welcome-step">
                            <div className="welcome-icon">🚀</div>
                            <h2>Get Started Quickly</h2>
                            <p>
                                With SynText AI, you can:
                            </p>
                            <ul className="feature-list">
                                <li>Upload documents to analyze</li>
                                <li>Generate flashcards and quizzes</li>
                                <li>Extract key concepts from any text</li>
                                <li>Ask questions about your documents</li>
                            </ul>
                        </div>
                    )}
                    
                    {step === 3 && (
                        <div className="welcome-step">
                            <div className="welcome-icon">🎉</div>
                            <h2>You're All Set!</h2>
                            <p>
                                Your account is ready to use. Try uploading your first document
                                or pasting some text to see SynText AI in action.
                            </p>
                            <p>Click "Get Started" to begin your journey!</p>
                        </div>
                    )}
                </div>
                
                <div className="welcome-actions">
                    {step < totalSteps ? (
                        <>
                            <button className="skip-button" onClick={handleSkip}>
                                Skip Tour
                            </button>
                            <button className="next-button" onClick={handleNext}>
                                Next
                            </button>
                        </>
                    ) : (
                        <button className="complete-button" onClick={handleNext}>
                            Get Started
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
};

export default Welcome;
