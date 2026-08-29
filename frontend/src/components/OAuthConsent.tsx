import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useUserContext } from '../UserContext';
import './OAuthConsent.css';

interface Workspace {
    id: number;
    name: string;
}

/**
 * The consent screen: an app is asking to read one workspace.
 *
 * The whole flow exists so that this page is the only place a decision is made.
 * The client says who it is and where to send the code; the person says which
 * workspace, or says no. Nothing the client sent decides what it gets.
 *
 * Two things are shown that a client cannot forge. The workspace list is fetched
 * with the person's own session, so it is what THEY can read rather than what
 * the client asked for. And the redirect host is printed beside the app's name,
 * because the name is a string the client chose about itself and the host is
 * where a code would actually be sent.
 */
const OAuthConsent: React.FC = () => {
    const [params] = useSearchParams();
    const { user, activeOrganizationId } = useUserContext();

    const clientId = params.get('client_id') || '';
    // The name the client registered for itself. Nothing verified it, which is
    // why the redirect host is printed next to it below.
    const clientName = params.get('client_name') || 'An application';
    const redirectUri = params.get('redirect_uri') || '';
    const scope = params.get('scope') || 'knowledge:read';
    const codeChallenge = params.get('code_challenge') || '';
    const codeChallengeMethod = params.get('code_challenge_method') || 'S256';
    const state = params.get('state');

    const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
    const [workspaceId, setWorkspaceId] = useState<number | null>(null);
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const missing = !clientId || !redirectUri || !codeChallenge;

    let redirectHost = '';
    try {
        redirectHost = redirectUri ? new URL(redirectUri).host : '';
    } catch {
        redirectHost = '';
    }

    const authHeader = useCallback(async () => {
        const token = await user!.getIdToken();
        return { Authorization: `Bearer ${token}` };
    }, [user]);

    useEffect(() => {
        if (!user || !activeOrganizationId || missing) {
            setLoading(false);
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const res = await fetch(
                    `/api/v1/workspaces?organization_id=${activeOrganizationId}`,
                    { headers: await authHeader() },
                );
                if (!res.ok) throw new Error(`status ${res.status}`);
                const body = await res.json();
                if (cancelled) return;
                const list: Workspace[] = body.workspaces || [];
                setWorkspaces(list);
                setWorkspaceId(list.length ? list[0].id : null);
            } catch {
                if (!cancelled) setError('Could not load your workspaces right now.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [user, activeOrganizationId, missing, authHeader]);

    const allow = async () => {
        if (workspaceId === null || submitting) return;
        setSubmitting(true);
        setError(null);
        try {
            const res = await fetch('/api/v1/oauth/authorize', {
                method: 'POST',
                headers: { ...(await authHeader()), 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    client_id: clientId,
                    redirect_uri: redirectUri,
                    workspace_id: workspaceId,
                    scopes: scope.split(' ').filter(Boolean),
                    code_challenge: codeChallenge,
                    code_challenge_method: codeChallengeMethod,
                    state,
                }),
            });
            if (!res.ok) throw new Error(`status ${res.status}`);
            const body = await res.json();
            // Leaving our origin entirely, so a full assignment rather than the
            // router: this is the handover back to the app that started it.
            window.location.href = body.redirect_to;
        } catch {
            setError('Could not complete that. Nothing was connected.');
            setSubmitting(false);
        }
    };

    const deny = () => {
        // Straight back to the app rather than to the client's redirect. Telling
        // the client "denied" hands control back to the thing that was just
        // refused, and there is nothing it needs to be told.
        window.location.hash = '#/settings';
    };

    if (missing) {
        return (
            <div className="consent">
                <p className="consent-error">
                    This authorization link is incomplete. Start again from the app
                    you were connecting.
                </p>
            </div>
        );
    }

    if (loading) return <p className="consent-status">Loading…</p>;

    return (
        <div className="consent">
            <h1 className="consent-title">Connect to SyntextAI</h1>

            <p className="consent-lead">
                <strong>{clientName}</strong>
                {redirectHost ? <> at <code>{redirectHost}</code></> : null}
                {' '}wants to read one of your workspaces.
            </p>

            <ul className="consent-grants">
                <li>Search the documents in the workspace you choose</li>
                <li>Read pages it finds, and cite them</li>
                <li>Nothing else. It cannot upload, delete, or change anything</li>
            </ul>

            {workspaces.length === 0 ? (
                <p className="consent-error">
                    You do not have a workspace to connect yet.
                </p>
            ) : (
                <label className="consent-field">
                    <span className="consent-label">Workspace</span>
                    <select
                        className="consent-input"
                        value={workspaceId ?? ''}
                        onChange={(e) => setWorkspaceId(Number(e.target.value))}
                    >
                        {workspaces.map((w) => (
                            <option key={w.id} value={w.id}>{w.name}</option>
                        ))}
                    </select>
                </label>
            )}

            <p className="consent-note">
                You can revoke this at any time in Settings, under Connections.
            </p>

            {error && <p className="consent-error">{error}</p>}

            <div className="consent-actions">
                <button type="button" className="consent-deny" onClick={deny}>
                    Cancel
                </button>
                <button
                    type="button"
                    className="consent-allow"
                    onClick={allow}
                    disabled={workspaceId === null || submitting}
                >
                    {submitting ? 'Connecting…' : 'Allow'}
                </button>
            </div>
        </div>
    );
};

export default OAuthConsent;
