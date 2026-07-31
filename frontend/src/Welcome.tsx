import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';
import './Welcome.css';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const Welcome: React.FC = () => {
    const navigate = useNavigate();
    const { user, darkMode, activeOrganizationId, setActiveOrganization } = useUserContext();
    const [step, setStep] = useState(1);
    const [companyName, setCompanyName] = useState('');
    const [isSaving, setIsSaving] = useState(false);
    const posthog = usePostHog();
    const totalSteps = 3;

    /**
     * Save the company name onto the organization created at signup.
     *
     * The organization already exists, so this renames rather than creates:
     * that avoids a half-made account if someone abandons onboarding, and it
     * means skipping simply leaves the generated name in place.
     */
    /**
     * Name the organization created at signup.
     *
     * Signing up starts a company, so the organization already exists by the
     * time onboarding runs. This renames it rather than creating anything,
     * which means abandoning onboarding just leaves the generated name in place.
     */
    const saveCompanyName = async () => {
        const name = companyName.trim();
        if (!name || !user) return;
        setIsSaving(true);
        try {
            const idToken = await user.getIdToken();

            // Onboarding runs before the organization chooser has set an active
            // one, so resolve it rather than assuming it is populated.
            let orgId = activeOrganizationId;
            if (!orgId) {
                const listed = await fetch('/api/v1/organizations', {
                    headers: { Authorization: `Bearer ${idToken}` },
                });
                if (listed.ok) {
                    const data = await listed.json();
                    const owned = (data.items || []).find((o: any) => o.role === 'owner');
                    if (owned) orgId = owned.organization_id;
                }
            }
            if (!orgId) return;

            const res = await fetch(`/api/v1/organizations/${orgId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${idToken}`,
                },
                body: JSON.stringify({ name }),
            });
            if (res.ok) await setActiveOrganization(orgId);
        } catch (e) {
            // Never block onboarding on this; the generated name stands.
            console.error('Error saving company name', e);
        } finally {
            setIsSaving(false);
        }
    };

    const handleNext = async () => {
        if (step === 1) {
            await saveCompanyName();
        }
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
                            {/* Organizations are created with a name derived from the
                                signup email, which reads as "drsmith's Organization".
                                That is what teammates see in invite emails and in the
                                organization chooser, so ask for the real one now. */}
                            <label className="welcome-label" htmlFor="org-name">
                                What's your company called?
                            </label>
                            <Input
                                id="org-name"
                                value={companyName}
                                onChange={(e) => setCompanyName(e.target.value)}
                                placeholder="Bayview Dental"
                                autoFocus
                            />
                            <p className="welcome-hint">
                                Your team will see this name when you invite them. You can change it later.
                            </p>
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
                                Choose a plan, then upload your first document and invite your team.
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
                            <Button onClick={handleNext} disabled={isSaving}>
                                {isSaving ? 'Saving...' : 'Next'}
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
