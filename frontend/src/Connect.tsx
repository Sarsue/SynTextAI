import React from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet';

import { useUserContext } from './UserContext';
import { Button } from '@/components/ui/button';
import './Home.css';
import './Connect.css';

/**
 * How to connect an AI client to a workspace.
 *
 * Public, and deliberately reachable without an account: half its job is
 * answering "does this work with the AI we already use" for somebody deciding
 * whether to buy. The other half is being the page a customer is sent to after
 * they ask.
 *
 * Every step here was walked with a real client before it was written. The
 * server address is the only thing a person types; the client discovers the
 * rest, which is why there is nothing on this page about endpoints or scopes.
 */
const Connect: React.FC = () => {
    const { darkMode } = useUserContext();

    return (
        <div className={`home ${darkMode ? 'dark-mode' : ''}`}>
            <Helmet>
                <title>Connect Claude to your documents | Syntext AI</title>
                <meta
                    name="description"
                    content="Connect Claude, or anything that speaks MCP, to your Syntext workspace. It answers from your own handbooks and policies, cited to the page. Read only, one workspace, revoke any time."
                />
            </Helmet>

            <header className="home-header">
                <Link to="/" className="home-logo" style={{ textDecoration: 'none' }}>Syntext</Link>
                <div className="home-header-actions">
                    <Link to="/login" className="home-link">Sign in</Link>
                    <Button asChild className="home-btn-primary">
                        <Link to="/signup">Sign up</Link>
                    </Button>
                </div>
            </header>

            <section className="home-hero">
                <p className="home-label">Connect your AI</p>
                <h1 className="home-headline">
                    Your team already asks Claude.<br />Let it answer from your documents.
                </h1>
                <p className="home-subtext">
                    Connect Claude to one Syntext workspace and it stops guessing. It
                    searches your own handbooks, policies and contracts, and every
                    answer links to the page it came from. Click the citation and the
                    document opens there.
                </p>
            </section>

            <div className="home-divider" />

            <section className="home-section">
                <h2 className="home-section-heading">Setup</h2>
                <div className="connect-steps">
                    <div className="connect-step">
                        <span className="connect-step-number">01</span>
                        <div>
                            <h3 className="connect-step-title">Add the connector</h3>
                            <p className="connect-step-text">
                                In Claude, add a connector and paste this address.
                            </p>
                            <code className="connect-code">https://syntextai.com/api/v1/mcp</code>
                        </div>
                    </div>

                    <div className="connect-step">
                        <span className="connect-step-number">02</span>
                        <div>
                            <h3 className="connect-step-title">Sign in to Syntext</h3>
                            <p className="connect-step-text">
                                Claude sends you here to sign in. Nothing is connected yet, and
                                Claude never sees your password.
                            </p>
                        </div>
                    </div>

                    <div className="connect-step">
                        <span className="connect-step-number">03</span>
                        <div>
                            <h3 className="connect-step-title">Choose a workspace, press Allow</h3>
                            <p className="connect-step-text">
                                You pick which workspace it may read. Claude does not get to
                                choose, and it only ever gets the one.
                            </p>
                        </div>
                    </div>
                </div>
            </section>

            <div className="home-divider" />

            <section className="home-section">
                <h2 className="home-section-heading">What it can and cannot do</h2>
                <div className="connect-columns">
                    <div className="connect-column">
                        <h3 className="connect-column-title">It can</h3>
                        <ul className="connect-list">
                            <li>Search the one workspace you chose</li>
                            <li>Read pages it finds there</li>
                            <li>Link you to the exact page, so you can check it yourself</li>
                        </ul>
                    </div>
                    <div className="connect-column">
                        <h3 className="connect-column-title">It cannot</h3>
                        <ul className="connect-list">
                            <li>Upload, edit or delete anything</li>
                            <li>Reach any other workspace</li>
                            <li>Do more than the person who connected it can do</li>
                        </ul>
                    </div>
                </div>
                <p className="connect-note">
                    That last one is worth saying plainly. The connection borrows the
                    permissions of whoever set it up, and it checks them on every
                    question. If that person leaves the workspace, the connection stops
                    working the same day. There is nothing to remember to switch off.
                </p>
            </section>

            <div className="home-divider" />

            <section className="home-section">
                <h2 className="home-section-heading">Turning it off</h2>
                <p className="connect-body">
                    Settings, then Connections. Every connected app is listed with when it
                    was last used, so you can tell what is still in use before you cut
                    anything off. Revoke stops it on the next question.
                </p>
            </section>

            <div className="home-divider" />

            <section className="home-section">
                <h2 className="home-section-heading">For scripts and developers</h2>
                <p className="connect-body">
                    If you are wiring this into something of your own rather than into
                    Claude, create an API key in Settings under Connections and send it as
                    a bearer token to the same address. The key is shown once. It carries
                    the same limits as above: one workspace, read only, and never more
                    than the person who created it.
                </p>
                <p className="connect-body connect-body--muted">
                    The server speaks the Model Context Protocol over HTTP, and
                    authenticates with OAuth 2.1 using PKCE. A client that speaks either
                    needs nothing from this page except the address.
                </p>
            </section>

            <div className="home-divider" />

            <section className="home-section connect-cta">
                <h2 className="home-section-heading">Nothing to connect yet?</h2>
                <p className="connect-body">
                    Bring your documents first. The connection is there when you want it.
                </p>
                <Button asChild className="home-btn-primary home-btn-lg">
                    <Link to="/signup">Get started</Link>
                </Button>
            </section>

            <footer className="home-footer">
                <span>© 2026 Osas Inc.</span>
            </footer>
        </div>
    );
};

export default Connect;
