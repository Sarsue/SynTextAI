import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';
import './Welcome.css';
import { Button } from '@/components/ui/button';

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
                                Syntext AI turns your company documents into an instant knowledge base your whole team can use.
                            </p>
                            <p>Let's get you set up in two quick steps.</p>
                        </div>
                    )}

                    {step === 2 && (
                        <div className="welcome-step">
                            <div className="welcome-icon">📂</div>
                            <h2>Upload your documents</h2>
                            <p>
                                Add your SOPs, policy manuals, employee handbooks, or any document your team needs to reference.
                            </p>
                            <ul className="feature-list">
                                <li>PDF and Word (.docx) files supported</li>
                                <li>Upload as many as you need</li>
                                <li>Your documents stay private to your workspace</li>
                            </ul>
                        </div>
                    )}

                    {step === 3 && (
                        <div className="welcome-step">
                            <div className="welcome-icon">🎉</div>
                            <h2>You're ready to go</h2>
                            <p>
                                Start your free trial, then upload your first document and invite your team.
                            </p>
                            <p>Your staff will get instant cited answers — and stop interrupting you.</p>
                        </div>
                    )}
                </div>
                
                <div className="welcome-actions">
                    {step < totalSteps ? (
                        <>
                            <Button variant="ghost" className="skip-button" onClick={handleSkip}>
                                Skip Tour
                            </Button>
                            <Button onClick={handleNext}>
                                Next
                            </Button>
                        </>
                    ) : (
                        <Button className="complete-button" onClick={handleNext}>
                            Get Started
                        </Button>
                    )}
                </div>
            </div>
        </div>
    );
};

export default Welcome;
