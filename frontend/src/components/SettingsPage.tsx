import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { getAuth, signOut } from 'firebase/auth';
import { X } from 'lucide-react';
import PaymentView from './PaymentView';
import DarkModeToggle from './DarkModeToggle';
import './SettingsPage.css'; // Import the CSS file
import { User } from 'firebase/auth';
import { useUserContext } from '../UserContext';
import UsagePanel from './UsagePanel';
import ConnectionsPanel from './ConnectionsPanel';
import TeamPanel from './TeamPanel';
import { Stripe } from '@stripe/stripe-js';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import ConfirmDialog from './ConfirmDialog';
import { useToast } from '../contexts/ToastContext';

interface SettingsPageProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null; // Adjust the user prop type
}

const SettingsPage: React.FC<SettingsPageProps> = ({ stripePromise, user }) => {
    const navigate = useNavigate();
    const { darkMode, setDarkMode, setUser, orgContext, activeOrganizationId, setActiveOrganization, clearActiveOrganization } = useUserContext();

    const { section } = useParams<{ section?: string }>();

    /* Every section, in the order they are worth reading: what you are paying
       for, who you are, who else is here, what they have been doing, what is
       connected, then the two that are settings in the ordinary sense.

       `when` decides whether the row appears. It mirrors what the API already
       enforces, so a hidden row is never the only thing standing between
       somebody and a panel they may not open. */
    const SECTIONS = [
        /* Named for what is behind it. An owner gets the payment form; somebody
           on a colleague's plan gets an explanation of what they are covered by,
           and no form. Labelling both "Plan" put a rail item and a panel heading
           side by side saying different words about the same section. */
        {
            id: 'plan',
            label: orgContext && !orgContext.can_manage_billing ? 'Plan' : 'Payment',
            when: true,
        },
        { id: 'organization', label: 'Organization', when: !!orgContext?.can_rename_organization },
        { id: 'team', label: 'Team', when: !!orgContext?.can_manage_members },
        { id: 'usage', label: 'Usage', when: !!orgContext?.can_manage_members },
        { id: 'connections', label: 'Connections', when: !!orgContext?.can_manage_members },
        { id: 'theme', label: 'Theme', when: true },
        { id: 'account', label: 'Delete Account', when: true },
    ];
    const visibleSections = SECTIONS.filter((s) => s.when);

    /* An unknown or hidden section falls back to the first visible one rather
       than rendering an empty panel. That covers a stale bookmark, a link to a
       section this person cannot see, and /settings with nothing after it. */
    const active = visibleSections.some((s) => s.id === section)
        ? (section as string)
        : (visibleSections[0]?.id ?? 'plan');
    const { addToast } = useToast();
    const [orgName, setOrgName] = React.useState('');
    const [isRenaming, setIsRenaming] = React.useState(false);
    const [confirmingDelete, setConfirmingDelete] = React.useState(false);

    // /chat is gated on the organization being entitled, so closing settings is
    // only meaningful once it is.
    const canLeaveSettings = Boolean(orgContext?.entitled);

    // How many other organizations this person could switch to. Fetched only
    // when they are stuck, so the way out is offered only when there is one.
    // What deleting this account would actually destroy.
    //
    // The copy below used to promise that documents in a shared company stay
    // with the company. That stopped being true: every company you own goes,
    // other members included, because you hold the card and your leaving ends
    // the subscription. Saying so in general terms would be alarming for
    // somebody who owns nothing and vague for somebody who owns a team, so the
    // real numbers are fetched and named.
    const [impact, setImpact] = React.useState<any>(null);
    React.useEffect(() => {
        if (!user) return;
        let cancelled = false;
        (async () => {
            try {
                const idToken = await user.getIdToken();
                const res = await fetch('/api/v1/users/deletion-impact', {
                    headers: { Authorization: `Bearer ${idToken}` },
                });
                if (!res.ok) return;
                const data = await res.json();
                if (!cancelled) setImpact(data);
            } catch {
                // The generic warning below still stands. Better a vaguer
                // caution than a confident number we could not fetch.
            }
        })();
        return () => { cancelled = true; };
    }, [user]);

    const [otherOrganizations, setOtherOrganizations] = React.useState(0);
    React.useEffect(() => {
        if (canLeaveSettings || !user) return;
        let cancelled = false;
        (async () => {
            try {
                const idToken = await user.getIdToken();
                const res = await fetch('/api/v1/organizations', {
                    headers: { Authorization: `Bearer ${idToken}` },
                });
                if (!res.ok) return;
                const data = await res.json();
                const others = (data.items || []).filter(
                    (o: any) => o.organization_id !== orgContext?.organization_id
                );
                if (!cancelled) setOtherOrganizations(others.length);
            } catch {
                // Leaves the link off. Better than offering a way out that
                // might loop straight back here.
            }
        })();
        return () => { cancelled = true; };
    }, [canLeaveSettings, user, orgContext?.organization_id]);

    // Seed the field once the organization context arrives.
    React.useEffect(() => {
        if (orgContext?.name) setOrgName(orgContext.name);
    }, [orgContext?.name]);

    const handleRenameOrganization = async () => {
        const name = orgName.trim();
        if (!name || !user || !activeOrganizationId) return;
        setIsRenaming(true);
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/organizations/${activeOrganizationId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${idToken}`,
                },
                body: JSON.stringify({ name }),
            });
            if (res.ok) {
                // Re-read the context so the new name is reflected everywhere.
                await setActiveOrganization(activeOrganizationId);
            } else {
                addToast('Could not rename the organization. Please try again.', 'error');
            }
        } catch (e) {
            console.error('Error renaming organization', e);
            addToast('Could not rename the organization. Please try again.', 'error');
        } finally {
            setIsRenaming(false);
        }
    };

    const handleDeleteAccount = async () => {
        setConfirmingDelete(false);

        if (!user) {
            addToast('No user found.', 'error');
            return;
        }

        try {
            const idToken = await user.getIdToken();
            if (!idToken) {
                console.error('User token not available');
                addToast('Authentication failed. Please try logging in again.', 'error');
                return;
            }

            const response = await fetch('/api/v1/users', {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${idToken}`,
                    'Content-Type': 'application/json',
                },
                mode: 'cors',
                credentials: 'include',
            });

            if (response.ok) {
                addToast('Your account has been deleted.', 'success');
                // The active organization is persisted, so without clearing it
                // a fresh signup would resolve back to the deleted account's
                // organization.
                clearActiveOrganization();
                // Same clean slate the normal sign-out gives, so nothing
                // outlives the account: the parked auth intent, cached ids,
                // anything else stashed for the session.
                sessionStorage.clear();

                // End the Firebase session, not just the React state.
                //
                // setUser(null) alone left the session live, so
                // onAuthStateChanged fired again and signed the deleted account
                // straight back in — against a user row that no longer exists.
                // Every subsequent request then failed, and the organization
                // chooser had nothing to show.
                try {
                    await signOut(getAuth());
                } catch (signOutError) {
                    // Already gone, or offline. The account is deleted either
                    // way, so leaving is still the right outcome.
                    console.error('Sign out after deletion failed:', signOutError);
                }
                setUser(null);
                // replace, so Back cannot return to a settings page belonging
                // to an account that no longer exists.
                navigate('/', { replace: true });
            } else {
                const errorData = await response.json().catch(() => ({}));
                console.error("Delete error:", errorData);
                // FastAPI returns {"detail": ...}, never {"error": ...}, so
                // reading .error showed 'Unknown error' for every real message.
                const detail = errorData?.detail ?? errorData?.error;
                addToast(
                    `Failed to delete account: ${typeof detail === 'string' ? detail : 'Unknown error'}`,
                    'error',
                );
            }
        } catch (error) {
            console.error("Error deleting account:", error);
            addToast('A network error occurred. Please try again later.', 'error');
        }
    };


    return (
        <div className={`settings-container ${darkMode ? 'dark-mode' : ''}`}>
            {/* Close, only when there is somewhere to go.

                /chat requires the organization to be entitled, so before a trial
                exists this button navigated to /chat and was bounced straight
                back here. It read as an unresponsive button when it was really a
                redirect loop. Hide it until closing actually leads somewhere, and
                say why below. */}
            {canLeaveSettings && (
                <Button
                    variant="ghost"
                    size="icon-sm"
                    className="close-button"
                    onClick={() => navigate('/chat')}
                >
                    <X className="size-4" />
                </Button>
            )}

            {/* A rail of sections, one panel at a time.

                This was one page with seven headings stacked down it, so Team
                did not fit and lived in a dialog beside the workspace list
                instead. A section per panel gives every one of them a name, a
                URL, and room to grow.

                The rail lists only what this person may actually open. Hiding a
                row is a courtesy, not the control: every panel behind it asks
                the API, which refuses on its own. */}
            <div className="settings-shell">
                <nav className="settings-rail" aria-label="Settings sections">
                    {visibleSections.map((s) => (
                        <button
                            key={s.id}
                            type="button"
                            className={`settings-rail-item${s.id === active ? ' is-active' : ''}`}
                            aria-current={s.id === active ? 'page' : undefined}
                            onClick={() => navigate(`/settings/${s.id}`)}
                        >
                            {s.label}
                        </button>
                    ))}
                </nav>

                <div className="settings-panel">
                    {!canLeaveSettings && (
                        <div className="settings-section">
                            <p className="text-sm">
                                <strong>{orgContext?.name || 'This organization'}</strong> has no
                                plan yet. Choose one below to start using it.
                            </p>
                            {/* An exit. Somebody who signed up for their own company
                                while already belonging to another was trapped here:
                                closing goes to /chat, /chat requires a plan, and it
                                bounced straight back with nothing to click. Only
                                offered when there is somewhere else to go, or the
                                chooser would auto-resolve and return them here. */}
                            {otherOrganizations > 0 && (
                                <p className="text-sm text-muted-foreground" style={{ marginTop: 8 }}>
                                    Or{' '}
                                    <button
                                        type="button"
                                        className="auth-link"
                                        onClick={() => navigate('/select-organization')}
                                    >
                                        switch to another organization
                                    </button>{' '}
                                    you belong to.
                                </p>
                            )}
                        </div>
                    )}

                    {active === 'plan' && (
                        <>
                            {/* Payment, owners only.

                                An invited member has no subscription of their own: their
                                access is paid for by whoever owns the workspace. Showing
                                them a payment form pushed them into starting a second,
                                duplicate trial for an organization that already pays. If
                                the owner's plan lapses they are told who to talk to, and
                                are never asked to fix somebody else's billing. */}
                            {orgContext && !orgContext.can_manage_billing ? (
                                <div className="settings-section">
                                    <h2 className="section-title">Plan</h2>
                                    <div className="section-content">
                                        {orgContext.entitled ? (
                                            <p className="text-sm text-muted-foreground">
                                                Your access is included in your team's plan. The workspace
                                                owner manages billing, so there's nothing for you to set up.
                                            </p>
                                        ) : (
                                            <p className="text-sm text-muted-foreground">
                                                Your team's plan needs attention, so some features are
                                                unavailable. Contact your workspace owner to restore access.
                                            </p>
                                        )}
                                    </div>
                                </div>
                            ) : (
                                <div className="settings-section">
                                    <h2 className="section-title">Payment</h2>
                                    <div className="section-content">
                                        <PaymentView
                                            stripePromise={stripePromise}
                                            user={user}
                                            darkMode={darkMode}
                                        />
                                    </div>
                                </div>
                            )}
                        </>
                    )}

                    {active === 'organization' && (
                        <>
                            {/* Organization, for owners and admins.

                                Organizations are created with a name derived from the signup
                                email, so they start out reading as "drsmith's Organization".
                                That is what teammates see in the chooser and what invite
                                emails announce, so it needs to be editable here and not only
                                during onboarding, which is easy to skip. */}
                            {orgContext?.can_rename_organization && (
                                <div className="settings-section">
                                    <h2 className="section-title">Organization</h2>
                                    <div className="section-content">
                                        <label className="settings-label" htmlFor="org-name-input">
                                            Company name
                                        </label>
                                        <div className="settings-inline-form">
                                            <Input
                                                id="org-name-input"
                                                value={orgName}
                                                onChange={(e) => setOrgName(e.target.value)}
                                                placeholder="Bayview Dental"
                                            />
                                            <Button
                                                onClick={handleRenameOrganization}
                                                disabled={
                                                    isRenaming ||
                                                    !orgName.trim() ||
                                                    orgName.trim() === (orgContext.name || '')
                                                }
                                            >
                                                {isRenaming ? 'Saving...' : 'Save'}
                                            </Button>
                                        </div>
                                        <p className="text-sm text-muted-foreground">
                                            Your team sees this name when you invite them.
                                        </p>
                                        <p className="text-sm text-muted-foreground">
                                            {orgContext.seats_used}{' '}
                                            {orgContext.seats_used === 1 ? 'member' : 'members'}
                                            {orgContext.seat_limit
                                                ? ` of ${orgContext.seat_limit} seats`
                                                : ''}
                                        </p>
                                    </div>
                                </div>
                            )}
                        </>
                    )}

                    {active === 'team' && (
                        <div className="settings-section">
                            <h2 className="section-title">Team</h2>
                            <TeamPanel />
                        </div>
                    )}

                    {active === 'usage' && (
                            <div className="settings-section">
                                <h2 className="section-title">Usage</h2>
                                <UsagePanel />
                            </div>
                    )}

                    {active === 'connections' && (
                        <>
                                {/* Under Usage rather than beside Organization: what has access
                                    and what it has been doing are the same question, and this
                                    is the screen somebody is already on when they ask it. */}
                                <div className="settings-section">
                                    <h2 className="section-title">Connections</h2>
                                    <ConnectionsPanel />
                                </div>
                        </>
                    )}

                    {active === 'theme' && (
                            <div className="settings-section">
                                <h2 className="section-title">Theme</h2>
                                <div className="section-content">
                                    <DarkModeToggle darkMode={darkMode} setDarkMode={setDarkMode} />
                                </div>
                            </div>
                    )}

                    {active === 'account' && (
                        <>
                                {/* Account Management Section */}
                                <div className="settings-section">
                                    <h2 className="section-title text-destructive">Delete Account</h2>
                                    <div className="section-content">
                                        {/* Says what actually happens. It promised to erase
                                            "uploaded files" full stop, which is wrong for a
                                            document sitting in a company workspace: that
                                            document belongs to the company, and erasing it
                                            would mean one person leaving takes their
                                            colleagues' knowledge base with them. */}
                                        <p className="text-sm text-muted-foreground">
                                            This permanently erases:
                                        </p>
                                        <ul className="list-disc ml-5 text-sm text-muted-foreground">
                                            <li>Your account, sign-in and payment details</li>
                                            <li>Your chat history</li>
                                            {(impact?.organizations || []).map((o: any) => (
                                                <li key={o.organization_id}>
                                                    <strong>{o.name}</strong>
                                                    {o.documents > 0 && `, and its ${o.documents} document${o.documents === 1 ? '' : 's'}`}
                                                    {o.other_members > 0 && `. ${o.other_members} other ${o.other_members === 1 ? 'person loses' : 'people lose'} access`}
                                                </li>
                                            ))}
                                            {!impact && <li>Any company you own, and everything in it</li>}
                                        </ul>
                                        {impact?.loses_access > 0 && (
                                            <p className="text-sm text-destructive">
                                                You own a company other people are using. Deleting your
                                                account ends it for them too: you hold the card, so the
                                                subscription goes with you and nobody can take it over.
                                            </p>
                                        )}
                                        <p className="text-sm text-muted-foreground">
                                            Documents in a company you only belong to stay with that
                                            company. It cannot be undone.
                                        </p>
                                        <Button variant="destructive" onClick={() => setConfirmingDelete(true)} className="w-fit">
                                            Delete My Account
                                        </Button>
                                    </div>
                                </div>
                        </>
                    )}
                </div>
            </div>

            <ConfirmDialog
                open={confirmingDelete}
                title="Delete your account?"
                // The last screen before it happens says the number, not a
                // category. "3 people lose access to 12 documents" is a
                // sentence somebody can weigh; "uploaded documents" is not.
                description={
                    impact?.loses_access > 0
                        ? `This deletes ${impact.organizations.map((o: any) => o.name).join(', ')}. `
                          + `${impact.loses_access} other ${impact.loses_access === 1 ? 'person loses' : 'people lose'} access`
                          + `${impact.documents_deleted > 0 ? ` and ${impact.documents_deleted} document${impact.documents_deleted === 1 ? '' : 's'} are deleted` : ''}. `
                          + 'It cannot be undone.'
                        : 'This permanently removes your payment details, chat history, uploaded documents and account credentials. It cannot be undone.'
                }
                confirmLabel="Delete my account"
                destructive
                onConfirm={handleDeleteAccount}
                onCancel={() => setConfirmingDelete(false)}
            />
        </div>
    );
};

export default SettingsPage;
