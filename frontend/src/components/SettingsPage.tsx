import React from 'react';
import { useNavigate } from 'react-router-dom';
import { getAuth, signOut } from 'firebase/auth';
import { X } from 'lucide-react';
import PaymentView from './PaymentView';
import DarkModeToggle from './DarkModeToggle';
import './SettingsPage.css'; // Import the CSS file
import { User } from 'firebase/auth';
import { useUserContext } from '../UserContext';
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
    const { addToast } = useToast();
    const [orgName, setOrgName] = React.useState('');
    const [isRenaming, setIsRenaming] = React.useState(false);
    const [confirmingDelete, setConfirmingDelete] = React.useState(false);

    // /chat is gated on the organization being entitled, so closing settings is
    // only meaningful once it is.
    const canLeaveSettings = Boolean(orgContext?.entitled);

    // How many other organizations this person could switch to. Fetched only
    // when they are stuck, so the way out is offered only when there is one.
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

            {/* Settings Content */}
            <div className="settings-content">
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

                {/* Theme Section */}
                <div className="settings-section">
                    <h2 className="section-title">Theme</h2>
                    <div className="section-content">
                        <DarkModeToggle darkMode={darkMode} setDarkMode={setDarkMode} />
                    </div>
                </div>

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
                            <li>
                                Any company you own on your own, and everything in it,
                                documents included
                            </li>
                        </ul>
                        <p className="text-sm text-muted-foreground">
                            Documents you uploaded into a company that has other people in
                            it stay with that company. It cannot be undone.
                        </p>
                        <Button variant="destructive" onClick={() => setConfirmingDelete(true)} className="w-fit">
                            Delete My Account
                        </Button>
                    </div>
                </div>
            </div>

            <ConfirmDialog
                open={confirmingDelete}
                title="Delete your account?"
                description="This permanently removes your payment details, chat history, uploaded documents and account credentials. It cannot be undone."
                confirmLabel="Delete my account"
                destructive
                onConfirm={handleDeleteAccount}
                onCancel={() => setConfirmingDelete(false)}
            />
        </div>
    );
};

export default SettingsPage;
