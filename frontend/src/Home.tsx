import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import './Home.css';
import { useUserContext } from './UserContext';
import { usePostHog } from './components/AnalyticsProvider';

const Home: React.FC = () => {
    const { darkMode } = useUserContext();
    const posthog = usePostHog();
    const [openFaqIndex, setOpenFaqIndex] = useState<number | null>(0);

    const demoEmbedUrl = 'https://www.youtube.com/embed/4oy5PdsxI4E';
    const pilotContactHref = 'https://calendly.com/osasigbinedion/30min';
    
    // Core features to showcase
    const features = [
        { 
            name: "Key Concepts with Citations", 
            icon: "Cite", 
            description: "Extract decision-relevant concepts and jump to the exact source page/timestamp"
        },
        { 
            name: "Due Diligence Speed", 
            icon: "Fast", 
            description: "Turn dense PDFs and long videos into an evidence-backed brief faster"
        },
        { 
            name: "Audit-Friendly Outputs", 
            icon: "Trace", 
            description: "Make defensible recommendations with an explicit trace back to the source"
        },
        { 
            name: "One Place for PDF + Video", 
            icon: "Media", 
            description: "Analyze reports and recordings in one workflow—no context switching"
        }
    ];

    const useCases = [
        {
            title: 'Due diligence briefs',
            description: 'Turn dense packets into citeable concepts you can defend in a memo or deck.'
        },
        {
            title: 'Market & competitor synthesis',
            description: 'Extract the key ideas and claims fast, with direct links back to source.'
        },
        {
            title: 'Policy & research notes',
            description: 'Build evidence-backed notes you can share with your team with traceability.'
        }
    ];

    const faqs = [
        {
            q: 'Is SynText AI self-serve?',
            a: 'Yes. You can sign in and use it immediately. Teams can also start with a short pilot to validate fit before committing annually.'
        },
        {
            q: 'How do citations work?',
            a: 'SynText AI links concepts back to the source so you can quickly verify context and defend outputs. PDFs link to pages; videos link to timestamps.'
        },
        {
            q: 'What formats do you support?',
            a: 'Today: PDFs, pasted text, and YouTube links. We are iterating based on consultant workflows and real pilot feedback.'
        },
        {
            q: 'How does pricing work?',
            a: 'SynText AI offers professional annual plans. For teams, we recommend starting with a pilot and then moving to an annual subscription.'
        }
    ];

    return (
        <div className={`app-container ${darkMode ? 'dark-mode' : ''}`}>
            <Helmet>
                <title>SynText AI - Source-Linked Key Concepts for Consultants & Analysts</title>
                <meta name="description" content="SynText AI turns dense PDFs and long videos into decision-ready key concepts with citations you can click. Built for consultants and analysts doing research, diligence, and client deliverables." />
                <meta name="keywords" content="consulting research, due diligence, document analysis, key concepts, citations, evidence, policy research, analyst tool" />
                <link rel="canonical" href="https://syntextai.com/" />
                <script type="application/ld+json">
                    {
                        JSON.stringify({
                            "@context": "https://schema.org",
                            "@type": "WebApplication",
                            "name": "SynText AI",
                            "description": "SynText AI turns dense PDFs and long videos into decision-ready key concepts with citations you can click.",
                            "applicationCategory": "BusinessApplication",
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
                <nav className="home-nav" aria-label="Primary">
                    <a className="home-nav-link" href="#demo" onClick={() => posthog.capture('homepage_nav_click', { target: 'demo' })}>Demo</a>
                    <a className="home-nav-link" href="#use-cases" onClick={() => posthog.capture('homepage_nav_click', { target: 'use_cases' })}>Use cases</a>
                    <a className="home-nav-link" href="#trust" onClick={() => posthog.capture('homepage_nav_click', { target: 'trust' })}>Security</a>
                    <a className="home-nav-link" href="#faq" onClick={() => posthog.capture('homepage_nav_click', { target: 'faq' })}>FAQ</a>
                </nav>
                <div className="auth-buttons">
                    <a
                        href={pilotContactHref}
                        className="signup-button"
                        target="_blank"
                        rel="noreferrer"
                        onClick={() => {
                            posthog.capture('homepage_request_pilot_click', { location: 'header' });
                        }}
                    >
                        Request a pilot
                    </a>
                    <Link
                        to="/login"
                        className="signin-link"
                        onClick={() => {
                            posthog.capture('homepage_sign_in_click', { location: 'header' });
                        }}
                    >
                        Sign in
                    </Link>
                </div>
            </header>

            {/* Main Hero with Search */}
            <main className="consensus-main">
                <div className="hero-section">
                    <div className="hero-content">
                        <h2 className="hero-title">Turn dense source material into citeable insights</h2>
                        <p className="hero-text">SynText AI helps consultants and analysts synthesize PDFs and long videos into key concepts with clickable citations—so you can ship defensible recommendations faster.</p>
                        <div className="home-cta-row">
                            <a
                                href={pilotContactHref}
                                className="signup-button"
                                target="_blank"
                                rel="noreferrer"
                                onClick={() => {
                                    posthog.capture('homepage_request_pilot_click', { location: 'hero' });
                                }}
                            >
                                Request a pilot
                            </a>
                            <a
                                href="#demo"
                                className="signin-link"
                                onClick={() => {
                                    posthog.capture('homepage_watch_demo_click', { location: 'hero' });
                                }}
                            >
                                Watch demo
                            </a>
                        </div>
                        <p className="home-pricing-anchor">Annual plans for professionals. Teams start with a pilot.</p>
                    </div>
                    <div className="home-social-proof" aria-label="Social proof">
                        <div className="home-proof-title">Built for research-heavy workflows</div>
                        <div className="home-proof-badges">
                            <span className="home-proof-badge">Diligence</span>
                            <span className="home-proof-badge">Research briefs</span>
                            <span className="home-proof-badge">Client deliverables</span>
                            <span className="home-proof-badge">Evidence review</span>
                        </div>
                    </div>
                </div>

                <section id="demo" className="home-demo-section">
                    <div className="home-section-header">
                        <h2 className="home-section-title">See it in action</h2>
                        <p className="home-section-subtitle">Watch how SynText AI turns dense source material into citeable concepts you can use in a brief.</p>
                    </div>
                    <div className="video-container">
                        <iframe
                            src={demoEmbedUrl}
                            frameBorder="0"
                            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                            referrerPolicy="strict-origin-when-cross-origin"
                            allowFullScreen
                            title="SynText AI Demo"
                        />
                    </div>
                </section>
                
                <section id="use-cases" className="home-use-cases">
                    <div className="home-section-header">
                        <h2 className="home-section-title">Use cases</h2>
                        <p className="home-section-subtitle">Start where the pain is highest: dense inputs, tight deadlines, and high stakes.</p>
                    </div>
                    <div className="home-cards-grid">
                        {useCases.map((uc) => (
                            <div key={uc.title} className="home-card">
                                <h3 className="home-card-title">{uc.title}</h3>
                                <p className="home-card-description">{uc.description}</p>
                            </div>
                        ))}
                    </div>
                </section>

                {/* Features Section */}
                <section className="features-section">
                    <div className="home-section-header">
                        <h2 className="home-section-title">One workflow for clarity + traceability</h2>
                        <p className="home-section-subtitle">Fast synthesis without losing the source of truth.</p>
                    </div>
                    <div className="features-grid">
                        {features.map((feature, index) => (
                            <div className="feature-item" key={index}>
                                <div className="feature-icon">{feature.icon}</div>
                                <h3 className="feature-title">{feature.name}</h3>
                                <p className="feature-description">{feature.description}</p>
                            </div>
                        ))}
                    </div>
                </section>

                <section id="trust" className="home-trust-section">
                    <div className="home-section-header">
                        <h2 className="home-section-title">Loved by users. Approved by teams.</h2>
                        <p className="home-section-subtitle">Built for professionals who need clarity and an audit trail.</p>
                    </div>
                    <div className="home-cards-grid">
                        <div className="home-card">
                            <h3 className="home-card-title">No extra tools</h3>
                            <p className="home-card-description">One workflow for PDFs and long videos—reduce context switching.</p>
                        </div>
                        <div className="home-card">
                            <h3 className="home-card-title">Traceability by design</h3>
                            <p className="home-card-description">Verify outputs quickly by jumping back to source pages and timestamps.</p>
                        </div>
                        <div className="home-card">
                            <h3 className="home-card-title">Team-ready</h3>
                            <p className="home-card-description">Start with a pilot, then move to an annual plan with support.</p>
                        </div>
                    </div>
                </section>

                <section id="faq" className="home-faq-section">
                    <div className="home-section-header">
                        <h2 className="home-section-title">You’ve likely got a few questions</h2>
                    </div>
                    <div className="home-faq">
                        {faqs.map((item, idx) => {
                            const isOpen = openFaqIndex === idx;
                            return (
                                <button
                                    key={item.q}
                                    type="button"
                                    className={`home-faq-item ${isOpen ? 'open' : ''}`}
                                    onClick={() => {
                                        setOpenFaqIndex(isOpen ? null : idx);
                                        posthog.capture('homepage_faq_toggle', { index: idx, open: !isOpen });
                                    }}
                                >
                                    <div className="home-faq-question">{item.q}</div>
                                    {isOpen ? <div className="home-faq-answer">{item.a}</div> : null}
                                </button>
                            );
                        })}
                    </div>
                </section>
                

                {/* Simple CTA */}
                <section className="home-bottom-cta">
                    <h2 className="pricing-title">Ready to move faster with evidence?</h2>
                    <p className="pricing-description">Try SynText AI and see how source-linked concepts fit your workflow.</p>
                    <div className="home-cta-row">
                        <a
                            href={pilotContactHref}
                            className="signup-button"
                            target="_blank"
                            rel="noreferrer"
                            onClick={() => {
                                posthog.capture('homepage_request_pilot_click', { location: 'bottom_cta' });
                            }}
                        >
                            Request a pilot
                        </a>
                        <Link 
                            to="/login" 
                            className="signin-link"
                            onClick={() => posthog.capture('cta_clicked')}
                        >
                            Try it now
                        </Link>
                    </div>
                </section>
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