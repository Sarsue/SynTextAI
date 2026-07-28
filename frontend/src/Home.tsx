import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import './Home.css';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';
import { Plus, Minus } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface Vertical {
    id: string;
    label: string;
    headline: string;
    documents: string[];
    examples: { q: string; a: string }[];
}

const verticals: Vertical[] = [
    {
        id: 'healthcare',
        label: 'Healthcare',
        headline: 'Your staff gets answers. You focus on patients.',
        documents: ['Patient intake procedures', 'Billing & coding guides', 'HIPAA compliance policies', 'Treatment protocols', 'Insurance pre-auth checklists'],
        examples: [
            { q: 'What is the pre-auth process for MRI scans?', a: 'Sourced from your Insurance Pre-Auth Policy, p.4' },
            { q: 'How do we handle a missed appointment fee?', a: 'Sourced from your Billing Procedures Manual, p.11' },
        ],
    },
    {
        id: 'accounting',
        label: 'Accounting',
        headline: 'Junior staff stop asking. Senior staff stop repeating.',
        documents: ['Client onboarding checklists', 'Filing deadline calendars', 'Compliance procedures', 'Engagement letter templates', 'Software SOPs'],
        examples: [
            { q: 'What documents do we need for a new corporate client?', a: 'Sourced from your Client Onboarding SOP, p.2' },
            { q: 'When is the T2 filing deadline for a December year-end?', a: 'Sourced from your Filing Deadlines Guide, p.7' },
        ],
    },
    {
        id: 'legal',
        label: 'Legal',
        headline: 'Find the precedent. Don\'t lose the billable hour.',
        documents: ['Matter intake procedures', 'Client communication standards', 'Filing deadlines & court rules', 'Billing & disbursement policies', 'Conflict check procedures'],
        examples: [
            { q: 'What is our conflict check process for new clients?', a: 'Sourced from your Intake Procedures Manual, p.3' },
            { q: 'What are the disbursement approval thresholds?', a: 'Sourced from your Billing Policy, p.9' },
        ],
    },
    {
        id: 'property',
        label: 'Property Management',
        headline: 'Your team handles it right the first time.',
        documents: ['Lease agreement templates', 'Maintenance request procedures', 'Tenant communication policies', 'Move-in / move-out checklists', 'Vendor contact directories'],
        examples: [
            { q: 'What is the notice period required before a property inspection?', a: 'Sourced from your Tenant Policy Manual, p.6' },
            { q: 'Who do we call for emergency HVAC repairs?', a: 'Sourced from your Vendor Directory, p.1' },
        ],
    },
    {
        id: 'trades',
        label: 'Trades & HVAC',
        headline: 'Techs get answers in the field. You stop getting calls.',
        documents: ['Installation specifications', 'Warranty & service policies', 'Safety procedures', 'Equipment manuals', 'Quote & pricing guides'],
        examples: [
            { q: 'What is the warranty period on Carrier heat pump installations?', a: 'Sourced from your Warranty Policy, p.2' },
            { q: 'What PPE is required for refrigerant handling?', a: 'Sourced from your Safety Procedures Manual, p.5' },
        ],
    },
    {
        id: 'insurance',
        label: 'Insurance',
        headline: 'Your agents look it up in seconds, not minutes.',
        documents: ['Product & coverage guides', 'Underwriting guidelines', 'Claims procedures', 'Compliance & licensing docs', 'Client communication scripts'],
        examples: [
            { q: 'What does our commercial general liability policy exclude?', a: 'Sourced from your Product Guide, p.14' },
            { q: 'What is the claims reporting window for property damage?', a: 'Sourced from your Claims Procedures, p.3' },
        ],
    },
];

const faqs = [
    {
        q: 'How is this different from searching a PDF?',
        a: 'Search requires you to know what to look for. Syntext lets staff ask questions in plain English and get a direct answer with the exact source cited, with no reading through results.',
    },
    {
        q: 'What file types do you support?',
        a: 'PDF and Word (.docx) files. If your SOPs, policies, or handbooks are in those formats, you are ready to go.',
    },
    {
        q: 'How accurate are the answers?',
        a: 'Syntext only answers from what is in your documents. If the answer is not there, it says so. Every answer includes a link to the source section so staff can verify.',
    },
    {
        q: 'How do I add my team?',
        a: 'Invite staff by email from inside the app. They click a link and they are in. No software to install.',
    },
    {
        q: 'Is my data private?',
        a: 'Your documents are only accessible to your workspace. We do not use your documents to train AI models.',
    },
    {
        q: 'Can I try before committing?',
        a: 'Yes. Every new workspace starts with a free trial. Upload your documents and invite your team before entering any payment details.',
    },
];

const Home: React.FC = () => {
    const { darkMode } = useUserContext();
    const posthog = usePostHog();
    const [openFaqIndex, setOpenFaqIndex] = useState<number | null>(null);
    const [activeVertical, setActiveVertical] = useState<string>(verticals[0].id);

    const vertical = verticals.find(v => v.id === activeVertical)!;

    return (
        <div className={`home ${darkMode ? 'dark-mode' : ''}`}>
            <Helmet>
                <title>Syntext AI: Instant answers from your company documents</title>
                <meta name="description" content="Upload your SOPs, policies, and manuals. Your staff gets instant cited answers." />
            </Helmet>

            {/* Header */}
            <header className="home-header">
                <span className="home-logo">Syntext</span>
                <div className="home-header-actions">
                    <Link to="/login" className="home-link" onClick={() => posthog.capture('homepage_sign_in_click')}>
                        Sign in
                    </Link>
                    <Button asChild className="home-btn-primary">
                        <Link to="/login" onClick={() => posthog.capture('homepage_get_started_click', { location: 'header' })}>
                            Get started
                        </Link>
                    </Button>
                </div>
            </header>

            {/* Hero */}
            <section className="home-hero">
                <p className="home-label">AI assistant for small business teams</p>
                <h1 className="home-headline">
                    Your documents.<br />Your team's answers.
                </h1>
                <p className="home-subtext">
                    Upload your SOPs, policies, and manuals. Staff get instant cited answers.<br />
                    You stop being interrupted.
                </p>
                <div className="home-actions">
                    <Button asChild className="home-btn-primary home-btn-lg">
                        <Link to="/login" onClick={() => posthog.capture('homepage_get_started_click', { location: 'hero' })}>
                            Start free trial
                        </Link>
                    </Button>
                    <Button asChild variant="outline" className="home-btn-ghost home-btn-lg">
                        <a href="mailto:osasigbinedion@gmail.com?subject=Syntext%20AI%20demo%20request" onClick={() => posthog.capture('homepage_demo_click')}>
                            Want a demo? Contact us
                        </a>
                    </Button>
                </div>
                <p className="home-footnote">No credit card required.</p>
            </section>

            <div className="home-divider" />

            {/* How it works */}
            <section className="home-section">
                <h2 className="home-section-heading">How it works</h2>
                <div className="home-steps">
                    {[
                        { n: '01', title: 'Upload your documents', body: 'Add your SOPs, policy manuals, or employee handbooks. PDF and Word files supported.' },
                        { n: '02', title: 'Invite your staff', body: 'Add team members by email. They get access immediately, no training required.' },
                        { n: '03', title: 'Staff get cited answers', body: 'Your team asks questions in plain English and gets answers with direct links to the source.' },
                    ].map(s => (
                        <div key={s.n} className="home-step">
                            <span className="home-step-n">{s.n}</span>
                            <h3 className="home-step-title">{s.title}</h3>
                            <p className="home-step-body">{s.body}</p>
                        </div>
                    ))}
                </div>
            </section>

            <div className="home-divider" />

            {/* Vertical selector */}
            <section className="home-section">
                <h2 className="home-section-heading">Built for your industry</h2>

                {/* Tabs */}
                <div className="home-vtabs" role="tablist">
                    {verticals.map(v => (
                        <Button
                            key={v.id}
                            variant="ghost"
                            role="tab"
                            aria-selected={activeVertical === v.id}
                            className={`home-vtab ${activeVertical === v.id ? 'home-vtab--active' : ''}`}
                            onClick={() => {
                                setActiveVertical(v.id);
                                posthog.capture('homepage_vertical_click', { vertical: v.id });
                            }}
                        >
                            {v.label}
                        </Button>
                    ))}
                </div>

                {/* Panel */}
                <div className="home-vpanel" role="tabpanel">
                    <div className="home-vpanel-left">
                        <p className="home-vpanel-headline">{vertical.headline}</p>
                        <p className="home-vpanel-label">Documents you'd upload</p>
                        <ul className="home-vpanel-docs">
                            {vertical.documents.map(d => <li key={d}>{d}</li>)}
                        </ul>
                    </div>
                    <div className="home-vpanel-right">
                        <p className="home-vpanel-label">Example questions your team asks</p>
                        <div className="home-vpanel-examples">
                            {vertical.examples.map(ex => (
                                <div key={ex.q} className="home-vexample">
                                    <p className="home-vexample-q">{ex.q}</p>
                                    <p className="home-vexample-a">{ex.a}</p>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            <div className="home-divider" />

            {/* Features */}
            <section className="home-section">
                <h2 className="home-section-heading">Built for operations, not IT</h2>
                <div className="home-features">
                    {[
                        { title: 'Cited answers', body: 'Every answer links to the exact section of your document. Staff can verify instantly.' },
                        { title: 'Shared workspace', body: 'One place for your whole team. Everyone gets the same accurate answer from the same source.' },
                        { title: 'Owner and staff roles', body: 'Owners manage documents and team members. Staff ask questions and read answers.' },
                        { title: 'No onboarding needed', body: 'If your staff can send a text, they can use Syntext. Invite by email and they are ready.' },
                    ].map(f => (
                        <div key={f.title} className="home-feature">
                            <h3 className="home-feature-title">{f.title}</h3>
                            <p className="home-feature-body">{f.body}</p>
                        </div>
                    ))}
                </div>
            </section>

            <div className="home-divider" />

            {/* Pricing */}
            <section className="home-section">
                <h2 className="home-section-heading">Pricing</h2>
                <p className="home-section-sub">One price per company. Add unlimited staff.</p>
                <div className="home-pricing">
                    <div className="home-plan">
                        <div className="home-plan-name">Starter</div>
                        <div className="home-plan-price">$99<span className="home-plan-period">/mo</span></div>
                        <ul className="home-plan-features">
                            <li>Up to 10 team members</li>
                            <li>Unlimited documents</li>
                            <li>Cited answers</li>
                            <li>PDF and Word support</li>
                            <li>Email support</li>
                        </ul>
                        <Button asChild variant="outline" className="home-btn-outline">
                            <Link to="/login" onClick={() => posthog.capture('homepage_pricing_click', { plan: 'starter' })}>
                                Get started
                            </Link>
                        </Button>
                    </div>
                    <div className="home-plan home-plan--featured">
                        <div className="home-plan-badge">Most popular</div>
                        <div className="home-plan-name">Business</div>
                        <div className="home-plan-price">$249<span className="home-plan-period">/mo</span></div>
                        <ul className="home-plan-features">
                            <li>Unlimited team members</li>
                            <li>Unlimited documents</li>
                            <li>Cited answers</li>
                            <li>PDF and Word support</li>
                            <li>Priority support</li>
                            <li>Onboarding call included</li>
                        </ul>
                        <Button asChild className="home-btn-primary">
                            <Link to="/login" onClick={() => posthog.capture('homepage_pricing_click', { plan: 'business' })}>
                                Get started
                            </Link>
                        </Button>
                    </div>
                </div>
                <p className="home-footnote">
                    Payments are securely handled by Stripe. Sign up and you're in, no setup call required.
                    Want a demo or a trial run first?{' '}
                    <a href="mailto:osasigbinedion@gmail.com?subject=Syntext%20AI%20demo%20request" onClick={() => posthog.capture('homepage_demo_click', { location: 'pricing' })}>
                        Reach out to us
                    </a>.
                </p>
            </section>

            <div className="home-divider" />

            {/* FAQ */}
            <section className="home-section home-section--narrow">
                <h2 className="home-section-heading">Questions</h2>
                <div className="home-faq">
                    {faqs.map((item, idx) => {
                        const open = openFaqIndex === idx;
                        return (
                            <Button
                                key={item.q}
                                variant="ghost"
                                className={`home-faq-row h-auto items-start ${open ? 'open' : ''}`}
                                onClick={() => {
                                    setOpenFaqIndex(open ? null : idx);
                                    posthog.capture('homepage_faq_toggle', { index: idx });
                                }}
                            >
                                <span className="home-faq-q">{item.q}</span>
                                <span className="home-faq-icon">{open ? <Minus className="size-4" /> : <Plus className="size-4" />}</span>
                                {open && <p className="home-faq-a">{item.a}</p>}
                            </Button>
                        );
                    })}
                </div>
            </section>

            <div className="home-divider" />

            {/* Bottom CTA */}
            <section className="home-cta">
                <h2 className="home-cta-heading">Stop answering the same questions twice.</h2>
                <Button asChild className="home-btn-primary home-btn-lg">
                    <Link to="/login" onClick={() => posthog.capture('homepage_get_started_click', { location: 'bottom' })}>
                        Start free trial
                    </Link>
                </Button>
            </section>

            {/* Footer */}
            <footer className="home-footer">
                <span>© 2026 Osas Inc.</span>
            </footer>
        </div>
    );
};

export default Home;
