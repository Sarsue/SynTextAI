import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAuth, signOut } from 'firebase/auth';
import { Building2, Loader2 } from 'lucide-react';
import { useUserContext } from '../UserContext';
import { Button } from '@/components/ui/button';

interface OrganizationSummary {
    organization_id: number;
    name: string;
    role: string;
    entitled: boolean;
    member_count: number;
}

/**
 * Asks which organization the user is signing into.
 *
 * A person can be staff at one company and the owner of their own, so the app
 * needs to know which tenant they are working in before anything is scoped.
 * Rather than a persistent switcher, the choice is made once at sign-in: for
 * the overwhelming majority who belong to exactly one organization this screen
 * never appears at all, and for everyone else the boundary is explicit instead
 * of being a dropdown that is easy to misread.
 */
const SelectOrganization: React.FC = () => {
    const navigate = useNavigate();
    const { user, darkMode, setActiveOrganization } = useUserContext();
    const [orgs, setOrgs] = useState<OrganizationSummary[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Signing out is the only honest exit from here.
    //
    // Deleting an account left a valid Firebase session pointing at a user row
    // that no longer exists, so this screen failed to load anything and offered
    // "Back to sign in" — which, still signed in, redirected straight back here.
    // A dead end you could not leave without clearing site data.
    const leave = useCallback(async (message?: string) => {
        try {
            await signOut(getAuth());
        } catch {
            // Already gone, or offline. Leaving is still the right outcome.
        }
        if (message) sessionStorage.setItem('signed_out_reason', message);
        // replace, so Back does not return to a screen that cannot load.
        navigate('/', { replace: true });
    }, [navigate]);

    useEffect(() => {
        if (!user) return;
        let cancelled = false;

        (async () => {
            try {
                const idToken = await user.getIdToken();
                const res = await fetch('/api/v1/organizations', {
                    headers: { Authorization: `Bearer ${idToken}` },
                });
                if (!res.ok) throw new Error('Could not load your organizations');
                const data = await res.json();
                const items: OrganizationSummary[] = data.items || [];
                if (cancelled) return;

                // Only ask when there is a genuine choice to make.
                if (items.length === 1) {
                    await setActiveOrganization(items[0].organization_id);
                    navigate('/chat', { replace: true });
                    return;
                }
                if (items.length === 0) {
                    // A real state now that signing in never creates anything:
                    // somebody signed in who has not signed up and holds no
                    // invitation. Send them out to the front page, where the
                    // choice between signing up and asking for a demo lives.
                    await leave('Sign up to start an organization, or ask the person who invited you to send the link again.');
                    return;
                }
                setOrgs(items);
            } catch (e) {
                if (cancelled) return;
                // Most often a deleted account: the session is still valid but
                // the user behind it is gone. Nothing here can recover, so end
                // the session rather than showing a button that loops.
                console.error('Could not load organizations:', e);
                await leave('Your session has ended. Sign in again to continue.');
            }
        })();

        return () => { cancelled = true; };
    }, [user, navigate, setActiveOrganization, leave]);

    const choose = async (org: OrganizationSummary) => {
        await setActiveOrganization(org.organization_id);
        navigate('/chat', { replace: true });
    };

    if (error) {
        return (
            <div className={`select-org ${darkMode ? 'dark-mode' : ''}`} style={styles.wrap}>
                <p style={styles.error}>{error}</p>
                <Button variant="outline" onClick={() => leave()}>
                    Sign out
                </Button>
            </div>
        );
    }

    if (!orgs) {
        return (
            <div style={styles.wrap}>
                <Loader2 className="size-6 animate-spin" />
            </div>
        );
    }

    return (
        <div className={`select-org ${darkMode ? 'dark-mode' : ''}`} style={styles.wrap}>
            <h1 style={styles.title}>Choose an organization</h1>
            <p style={styles.sub}>You belong to more than one. Pick the one you're working in.</p>

            <div style={styles.list}>
                {orgs.map(org => (
                    <button key={org.organization_id} onClick={() => choose(org)} style={styles.card}>
                        <Building2 className="size-5" />
                        <span style={styles.cardBody}>
                            <span style={styles.name}>{org.name}</span>
                            <span style={styles.meta}>
                                {/* Staff, not Member. 'member' was removed from the
                                    code this morning for being a third word for a
                                    role that already had one, and it survived here. */}
                                {org.role === 'owner' ? 'Owner' : org.role === 'admin' ? 'Admin' : 'Staff'}
                                {' · '}
                                {org.member_count} {org.member_count === 1 ? 'person' : 'people'}
                                {!org.entitled && ' · plan needs attention'}
                            </span>
                        </span>
                    </button>
                ))}
            </div>
        </div>
    );
};

const styles: Record<string, React.CSSProperties> = {
    wrap: {
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '0.75rem',
        padding: '2rem',
    },
    title: { fontSize: '1.5rem', fontWeight: 600, margin: 0 },
    sub: { opacity: 0.7, margin: 0, textAlign: 'center' },
    list: { display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '1rem', width: '100%', maxWidth: '26rem' },
    card: {
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        padding: '1rem',
        border: '1px solid var(--border, #d4d4d8)',
        borderRadius: '0.5rem',
        background: 'transparent',
        cursor: 'pointer',
        textAlign: 'left',
        font: 'inherit',
        color: 'inherit',
        width: '100%',
    },
    cardBody: { display: 'flex', flexDirection: 'column' },
    name: { fontWeight: 600 },
    meta: { fontSize: '0.85rem', opacity: 0.7 },
    error: { color: 'var(--destructive, #dc2626)' },
};

export default SelectOrganization;
