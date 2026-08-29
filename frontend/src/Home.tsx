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
    /** Documents this industry never gets round to writing. Concrete beats
     *  "it writes documents": a practice manager recognises her own missing
     *  intake checklist and does not recognise a capability. */
    writes: string[];
    examples: { q: string; a: string }[];
}

const verticals: Vertical[] = [
    {
        id: 'healthcare',
        label: 'Healthcare',
        headline: 'Your staff gets answers. You focus on patients.',
        documents: ['Patient intake procedures', 'Billing & coding guides', 'HIPAA compliance policies', 'Treatment protocols', 'Insurance pre-auth checklists'],
        writes: ['A new-patient intake checklist', 'A one page infection control summary', 'A front-desk script for insurance questions'],
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
        writes: ['A year-end close checklist', 'A new-client onboarding brief', 'A one page guide to your filing deadlines'],
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
        writes: ['A matter intake checklist', 'A client onboarding letter', 'A one page summary of your billing policy'],
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
        writes: ['A move-out inspection checklist', 'A tenant welcome pack', 'A one page emergency contact sheet'],
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
        writes: ['A pre-job safety checklist', 'A van stock list for a service call', 'A one page warranty summary for customers'],
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
        writes: ['A claims intake checklist', 'A one page coverage comparison', 'A renewal call script'],
        examples: [
            { q: 'What does our commercial general liability policy exclude?', a: 'Sourced from your Product Guide, p.14' },
            { q: 'What is the claims reporting window for property damage?', a: 'Sourced from your Claims Procedures, p.3' },
        ],
    },
    {
        id: 'manufacturing',
        label: 'Manufacturing',
        headline: 'Technicians get the fix. Machines stay running.',
        documents: ['Equipment manuals', 'Maintenance SOPs', 'Torque & spec sheets', 'Safety procedures', 'Troubleshooting guides'],
        writes: ['A shift handover checklist', 'A one page lockout/tagout summary', 'A preventive maintenance schedule'],
        examples: [
            { q: 'What is the torque spec for the drive shaft bolts on the CNC lathe?', a: 'Sourced from your Equipment Manual, p.22' },
            { q: 'What is the lockout/tagout procedure before servicing the conveyor motor?', a: 'Sourced from your Safety Procedures Manual, p.8' },
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
        a: 'PDF, Word (.docx), text and Markdown. Drag them in, or import them straight from Google Drive without downloading anything first. Anything Syntext writes comes back out as Word or PDF.',
    },
    {
        q: 'Can it write documents, or only answer questions?',
        a: 'Both. Describe the checklist, SOP or summary you need and it writes one from the documents already in your workspace, and tells you which ones it used. You edit it, download it as Word or PDF, and choose whether to add it to your knowledge base.',
    },
    {
        q: 'Does what it writes become an answer straight away?',
        a: 'No, and that is deliberate. A document it wrote answers nothing until you have read it and added it yourself. Otherwise its own writing would become its own source, and nobody reading an answer could tell the difference.',
    },
    {
        q: 'What happens when a policy is replaced?',
        a: 'Mark the old one as replaced by the new one. It stops appearing in answers immediately and stays on file, so nobody gets last year\'s rule quoted back at them. One click puts it back.',
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
        a: 'Get in touch and we will set you up with a working workspace on your own documents for a limited time. No card, no setup on your end.',
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
                <meta name="description" content="Upload your SOPs, policies and manuals. Your staff gets instant cited answers, and Syntext writes the documents you are missing from the same material." />
            </Helmet>

            {/* Header */}
            <header className="home-header">
                <span className="home-logo">Syntext</span>
                <div className="home-header-actions">
                    <Link to="/login" className="home-link" onClick={() => posthog.capture('homepage_sign_in_click')}>
                        Sign in
                    </Link>
                    <Button asChild className="home-btn-primary">
                        <Link to="/signup" onClick={() => posthog.capture('homepage_get_started_click', { location: 'header' })}>
                            Sign up
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
                    Upload your SOPs and policies. Staff get cited answers in seconds.<br />
                    Need a document you never wrote? It writes that too.
                </p>
                <div className="home-actions">
                    <Button asChild className="home-btn-primary home-btn-lg">
                        <Link to="/signup" onClick={() => posthog.capture('homepage_get_started_click', { location: 'hero' })}>
                            Get started
                        </Link>
                    </Button>
                    <Button asChild variant="outline" className="home-btn-ghost home-btn-lg">
                        <a href="mailto:osas@osas-inc.com?subject=Syntext%20AI%20demo%20request" onClick={() => posthog.capture('homepage_demo_click')}>
                            Want a demo? Contact us
                        </a>
                    </Button>
                </div>
                {/* The price is stated, not whispered. Guru and Glean cannot
                    put a number on a page, because enterprise sales will not
                    let them: every visitor has to talk to somebody first. A
                    number and a sign-up button is the one thing this product
                    can do that they structurally cannot, and it spent its first
                    year set in 10px grey under the buttons.

                    It is, in fact, required. The trial was dropped rather than
                    built, so signing up takes a card and an organization with
                    no subscription is entitled to nothing. Promising otherwise
                    sold the one thing the product does not do, and the person
                    who believed it found out at the payment form. */}
                {/* A price in the hero, because Guru and Glean cannot put one on a
                    page at all and making somebody scroll past four sections to
                    learn the cost throws that away.

                    But not a giant $99 shouting "the whole practice". There are
                    two plans and $99 covers ten seats, so a twenty-five person
                    practice read the hero, scrolled to the cards, and found
                    $249: a small broken promise discovered at the exact moment
                    they were deciding. The cards carry the detail; this says
                    enough to know it is affordable and nothing it has to walk
                    back. */}
                <p className="home-price">
                    <strong>$99 a month</strong> for 10 people, then $9 each.
                    <span className="home-price-meta">Cancel anytime.</span>
                </p>

                {/* One line, where the objection actually lands. "We already use
                    ChatGPT" is the standard brush-off, and the answer is not a
                    feature block explaining a protocol nobody here has heard of.
                    It is a sentence and a link, for the minority who care. */}
                <p className="home-price-meta home-connect-line">
                    Already using Claude?{' '}
                    <Link
                        to="/connect"
                        className="home-link"
                        onClick={() => posthog.capture('homepage_connect_click')}
                    >
                        Connect it to your documents
                    </Link>.
                </p>
            </section>

            {/* The product, in the first screen. A real answer, from real
                documents, cited to the page it came from. Nobody buys software
                they have not seen, and every competitor shows theirs. */}
            <section className="home-shot home-shot--hero">
                <img
                    src="/product/answer.png"
                    alt="A question about a cancellation fee, answered with the exact figures and a link to page 4 of the practice's own intake procedure."
                    width={1200}
                    height={1030}
                    loading="eager"
                />
                <p className="home-shot-caption">
                    A real answer, cited to the page it came from.
                </p>
            </section>

            <div className="home-divider" />

            {/* How it works */}
            <section className="home-section">
                <h2 className="home-section-heading">How it works</h2>
                <div className="home-steps">
                    {[
                        { n: '01', title: 'Bring your documents', body: 'Import straight from Google Drive, or drag in PDFs and Word files. Your SOPs, policy manuals and handbooks.' },
                        { n: '02', title: 'Invite your staff', body: 'Add team members by email. They get access immediately, no training required.' },
                        { n: '03', title: 'Staff get cited answers', body: 'Your team asks questions in plain English and gets answers with direct links to the source.' },
                        { n: '04', title: 'Ask for the ones you are missing', body: 'Describe a checklist or an SOP and Syntext writes it from your own documents. Edit it, download it as Word or PDF, and add it back when you are happy with it.' },
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
                        <p className="home-vpanel-label">Documents you'd ask it to write</p>
                        <ul className="home-vpanel-docs home-vpanel-writes">
                            {vertical.writes.map(d => <li key={d}>{d}</li>)}
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

            {/* Three things, each one shown rather than described. The grid
                of six one-line cards that was here said "cited answers" and
                "shared workspace" and left a stranger no wiser about what the
                product looks like. */}
            <section className="home-section">
                <h2 className="home-section-heading">Three things it does</h2>

                <div className="home-thing">
                    <div className="home-thing-text">
                        <span className="home-thing-n">01</span>
                        <h3>Answers, with the page attached</h3>
                        <p>
                            Staff ask in plain English. The answer comes back with the
                            figure and the page it came from.
                        </p>
                        <p className="home-thing-note">
                            Not in your documents? It says so instead of inventing one.
                        </p>
                    </div>
                    <div className="home-thing-shot">
                        <img
                            src="/product/answer.png"
                            alt="A cited answer about a cancellation fee, sourced to page 4 of the practice's intake procedure."
                            width={1200} height={1030} loading="lazy"
                        />
                    </div>
                </div>

                <div className="home-thing home-thing--reverse">
                    <div className="home-thing-text">
                        <span className="home-thing-n">02</span>
                        <h3>The documents you never got round to writing</h3>
                        <p>
                            Describe what you need. It writes one from the documents you
                            already have, and hands you a Word or PDF file.
                        </p>
                        <p className="home-thing-note">
                            Nothing it writes answers a question until you add it yourself.
                        </p>
                    </div>
                    <div className="home-thing-shot">
                        <img
                            src="/product/document.png"
                            alt="A front desk onboarding checklist written by SyntextAI, marked as written by AI, with Word and PDF buttons and the three source documents named."
                            width={1200} height={1260} loading="lazy"
                        />
                    </div>
                </div>

                <div className="home-thing">
                    <div className="home-thing-text">
                        <span className="home-thing-n">03</span>
                        <h3>One box. Ask, find, or write.</h3>
                        <p>
                            The same box does all three. If your staff can send a text
                            message, they can use it.
                        </p>
                        <p className="home-thing-note">
                            Replaced a policy? Mark the old one and it stops turning up.
                        </p>
                    </div>
                    <div className="home-thing-shot">
                        <img
                            src="/product/write.png"
                            alt="The composer with Ask, Find and Write, set to Write, with a request for a front desk onboarding checklist."
                            width={1200} height={478} loading="lazy"
                        />
                    </div>
                </div>
            </section>

            <div className="home-divider" />

            {/* Pricing */}
            <section className="home-section">
                <h2 className="home-section-heading">Pricing</h2>
                <p className="home-section-sub">One price per company. Seats included, then pay only for who you add.</p>
                <div className="home-pricing">
                    <div className="home-plan">
                        <div className="home-plan-name">Starter</div>
                        <div className="home-plan-price">$99<span className="home-plan-period">/mo</span></div>
                        <ul className="home-plan-features">
                            <li>10 seats included, then $9 each</li>
                            <li>Unlimited documents</li>
                            <li>Cited answers</li>
                            <li>Written documents, exported to Word or PDF</li>
                            <li>PDF and Word in, or import from Google Drive</li>
                            <li>Email support</li>
                        </ul>
                        <Button asChild variant="outline" className="home-btn-outline">
                            <Link to="/signup" onClick={() => posthog.capture('homepage_pricing_click', { plan: 'starter' })}>
                                Get started
                            </Link>
                        </Button>
                    </div>
                    <div className="home-plan home-plan--featured">
                        <div className="home-plan-badge">Most popular</div>
                        <div className="home-plan-name">Business</div>
                        <div className="home-plan-price">$249<span className="home-plan-period">/mo</span></div>
                        <ul className="home-plan-features">
                            <li>30 seats included, then $7 each</li>
                            <li>Unlimited documents</li>
                            <li>Cited answers</li>
                            <li>Written documents, exported to Word or PDF</li>
                            <li>PDF and Word in, or import from Google Drive</li>
                            <li>Priority support</li>
                            <li>Onboarding call included</li>
                        </ul>
                        <Button asChild className="home-btn-primary">
                            <Link to="/signup" onClick={() => posthog.capture('homepage_pricing_click', { plan: 'business' })}>
                                Get started
                            </Link>
                        </Button>
                    </div>
                </div>
                <p className="home-footnote">
                    Payments are securely handled by Stripe. Add a card and you're in, no setup call required.
                    Seats are added as you invite people and removed the moment you remove them.
                    Want to see it on your own documents first?{' '}
                    <a href="mailto:osas@osas-inc.com?subject=Syntext%20AI%20demo%20request" onClick={() => posthog.capture('homepage_demo_click', { location: 'pricing' })}>
                        Reach out to us
                    </a>.
                </p>
            </section>

            <div className="home-divider" />

            {/* One signed section. Guru's homepage has a company behind it and
                no person on it; this is the one thing they cannot copy, and it
                costs a paragraph. Deliberately short: the product carries the
                page, this just says who is behind it. */}
            <section className="home-section home-section--narrow">
                <div className="home-note">
                    <p>
                        The tools that do this well start at a sales call and a five figure
                        contract. A twelve person practice is never getting either.
                    </p>
                    <p>
                        So it is $99, you sign up with a card, and it works on the files you
                        already have. Email me and I will set it up on your own documents.
                    </p>
                    <p className="home-note-sign">
                        Osas Igbinedion, who builds it and answers the email<br />
                        <a href="mailto:osas@osas-inc.com?subject=Syntext%20AI">
                            osas@osas-inc.com
                        </a>
                    </p>
                </div>
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
                    <Link to="/signup" onClick={() => posthog.capture('homepage_get_started_click', { location: 'bottom' })}>
                        Get started
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
