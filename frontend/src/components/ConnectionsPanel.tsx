import React, { useCallback, useEffect, useState } from 'react';

import { useUserContext } from '../UserContext';
import { formatServerTime } from '../utils/serverTime';
import './ConnectionsPanel.css';

interface Workspace {
    id: number;
    name: string;
}

interface Connection {
    // Not unique on its own. Keys and grants are separate tables that number
    // their rows independently, so every request and every React key has to
    // carry `kind` alongside it.
    id: number;
    kind: 'api_key' | 'oauth';
    name: string;
    prefix: string;
    scopes: string[];
    created_at: string | null;
    last_used_at: string | null;
    revoked_at: string | null;
    expires_at: string | null;
}

/** Expiry offered as choices, because a date picker asks a question nobody has
 *  an answer to. Never is first and is the default: forced rotation nobody
 *  asked for mostly produces integrations that break silently on a Tuesday. */
const EXPIRY_CHOICES: { label: string; days: number | null }[] = [
    { label: 'Never', days: null },
    { label: '30 days', days: 30 },
    { label: '90 days', days: 90 },
    { label: '1 year', days: 365 },
];

const expiryToIso = (days: number | null): string | null => {
    if (days === null) return null;
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString();
};

/**
 * Connections: the credentials that let something other than a browser read a
 * workspace.
 *
 * Named for what will be here, not only for what is. Today every row is an API
 * key; an app authorized through OAuth is the same question to the person
 * reading this screen — what has access, when did it last use it, and how do I
 * stop it — so it belongs in this list rather than in a second one built later.
 *
 * Scoped to one workspace at a time, deliberately. A credential is issued for a
 * single workspace, so choosing which one is part of creating it, not a filter
 * applied afterwards to a list of everything.
 */
const ConnectionsPanel: React.FC = () => {
    const { user, activeOrganizationId, orgContext } = useUserContext();

    const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
    const [workspaceId, setWorkspaceId] = useState<number | null>(null);
    const [connections, setConnections] = useState<Connection[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [name, setName] = useState('');
    const [expiryDays, setExpiryDays] = useState<number | null>(null);
    const [creating, setCreating] = useState(false);
    // The plaintext token, held only in this component's state and only until
    // the page is left. It is not stored anywhere and the API cannot return it
    // again, which is the whole reason this banner has to be unmissable.
    const [newToken, setNewToken] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);

    // The same approximation UsagePanel makes, and for the same reason: this is
    // an owner-and-admin surface, the API asserts MANAGE_API_KEYS per workspace,
    // and hiding it here is a courtesy rather than the control. Defaults to
    // hidden while the context loads, so a member never sees a section appear
    // and then fail.
    const canManage = orgContext?.can_manage_members ?? false;

    const authHeader = useCallback(async () => {
        const token = await user!.getIdToken();
        return { Authorization: `Bearer ${token}` };
    }, [user]);

    useEffect(() => {
        if (!user || !activeOrganizationId || !canManage) {
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
                if (!cancelled) setError('Could not load workspaces right now.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [user, activeOrganizationId, canManage, authHeader]);

    const loadConnections = useCallback(async (id: number) => {
        try {
            const res = await fetch(`/api/v1/workspaces/${id}/api-keys`, {
                headers: await authHeader(),
            });
            if (!res.ok) throw new Error(`status ${res.status}`);
            setConnections(await res.json());
            setError(null);
        } catch {
            setError('Could not load connections right now.');
        }
    }, [authHeader]);

    useEffect(() => {
        if (workspaceId === null) return;
        // A token belongs to the workspace it was made in. Leaving it on screen
        // while the list underneath changes to a different workspace is how
        // somebody pastes it into the wrong place.
        setNewToken(null);
        loadConnections(workspaceId);
    }, [workspaceId, loadConnections]);

    const create = async (event: React.FormEvent) => {
        event.preventDefault();
        if (workspaceId === null || !name.trim() || creating) return;
        setCreating(true);
        setError(null);
        try {
            const res = await fetch(`/api/v1/workspaces/${workspaceId}/api-keys`, {
                method: 'POST',
                headers: { ...(await authHeader()), 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name.trim(),
                    expires_at: expiryToIso(expiryDays),
                }),
            });
            if (!res.ok) throw new Error(`status ${res.status}`);
            const created = await res.json();
            setNewToken(created.token);
            setCopied(false);
            setName('');
            await loadConnections(workspaceId);
        } catch {
            setError('Could not create the connection. Try again.');
        } finally {
            setCreating(false);
        }
    };

    const revoke = async (connection: Connection) => {
        if (workspaceId === null) return;
        // Named, not "this key". Somebody with four rows on screen is choosing
        // between them, and the name is the only thing telling them apart.
        const sure = window.confirm(
            `Revoke "${connection.name}"? Anything using it stops working immediately.`,
        );
        if (!sure) return;
        try {
            const res = await fetch(
                `/api/v1/workspaces/${workspaceId}/api-keys/${connection.id}`
                + `?kind=${connection.kind}`,
                { method: 'DELETE', headers: await authHeader() },
            );
            if (!res.ok) throw new Error(`status ${res.status}`);
            await loadConnections(workspaceId);
        } catch {
            setError('Could not revoke that connection.');
        }
    };

    const copy = async () => {
        if (!newToken) return;
        try {
            await navigator.clipboard.writeText(newToken);
            setCopied(true);
        } catch {
            // Clipboard access can be refused. The token is on screen and
            // selectable, so this is a missing convenience rather than a
            // failure worth an error banner.
            setCopied(false);
        }
    };

    if (!canManage) return null;
    if (loading) return <p className="connections-status">Loading connections…</p>;

    if (!workspaces.length) {
        return (
            <p className="connections-status">
                Create a workspace first. A connection is issued for one workspace.
            </p>
        );
    }

    const isDead = (c: Connection) =>
        c.revoked_at !== null ||
        (c.expires_at !== null && new Date(c.expires_at) <= new Date());

    return (
        <div className="connections-panel">
            <p className="connections-intro">
                Let something other than a browser read one workspace: Claude, a
                script, a report. Read only, and it can never do more than the
                person who created it.
            </p>

            <label className="connections-field">
                <span className="connections-label">Workspace</span>
                <select
                    className="connections-input"
                    value={workspaceId ?? ''}
                    onChange={(e) => setWorkspaceId(Number(e.target.value))}
                >
                    {workspaces.map((w) => (
                        <option key={w.id} value={w.id}>{w.name}</option>
                    ))}
                </select>
            </label>

            {newToken && (
                <div className="connections-token">
                    <p className="connections-token-warning">
                        Copy this now. It is not stored and cannot be shown again.
                    </p>
                    <code className="connections-token-value">{newToken}</code>
                    <button type="button" className="connections-copy" onClick={copy}>
                        {copied ? 'Copied' : 'Copy'}
                    </button>
                </div>
            )}

            <form className="connections-create" onSubmit={create}>
                <label className="connections-field">
                    <span className="connections-label">Name</span>
                    <input
                        className="connections-input"
                        type="text"
                        value={name}
                        maxLength={100}
                        placeholder="Claude desktop"
                        onChange={(e) => setName(e.target.value)}
                    />
                </label>
                <label className="connections-field">
                    <span className="connections-label">Expires</span>
                    <select
                        className="connections-input"
                        value={expiryDays === null ? '' : String(expiryDays)}
                        onChange={(e) =>
                            setExpiryDays(e.target.value === '' ? null : Number(e.target.value))
                        }
                    >
                        {EXPIRY_CHOICES.map((choice) => (
                            <option
                                key={choice.label}
                                value={choice.days === null ? '' : String(choice.days)}
                            >
                                {choice.label}
                            </option>
                        ))}
                    </select>
                </label>
                <button
                    type="submit"
                    className="connections-button"
                    disabled={!name.trim() || creating}
                >
                    {creating ? 'Creating…' : 'Create connection'}
                </button>
            </form>

            {error && <p className="connections-status">{error}</p>}

            {connections.length === 0 ? (
                <p className="connections-status">Nothing is connected to this workspace.</p>
            ) : (
                <ul className="connections-list">
                    {connections.map((c) => (
                        <li
                            key={`${c.kind}-${c.id}`}
                            className={`connections-row${isDead(c) ? ' connections-row-dead' : ''}`}
                        >
                            <div className="connections-row-main">
                                <span className="connections-name">{c.name}</span>
                                <code className="connections-prefix">
                                    {c.kind === 'oauth'
                                        ? 'Connected app'
                                        : `stx_live_${c.prefix}…`}
                                </code>
                            </div>
                            <div className="connections-row-meta">
                                {/* Last use is the column that makes somebody
                                    willing to revoke: nobody deletes a
                                    credential when they cannot tell whether
                                    anything still depends on it. */}
                                <span>
                                    {c.last_used_at
                                        ? `Last used ${formatServerTime(c.last_used_at)}`
                                        : 'Never used'}
                                </span>
                                {c.revoked_at && (
                                    <span>Revoked {formatServerTime(c.revoked_at)}</span>
                                )}
                                {!c.revoked_at && c.expires_at && (
                                    <span>
                                        {new Date(c.expires_at) <= new Date()
                                            ? `Expired ${formatServerTime(c.expires_at)}`
                                            : `Expires ${formatServerTime(c.expires_at)}`}
                                    </span>
                                )}
                            </div>
                            {!c.revoked_at && (
                                <button
                                    type="button"
                                    className="connections-revoke"
                                    onClick={() => revoke(c)}
                                >
                                    Revoke
                                </button>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

export default ConnectionsPanel;
