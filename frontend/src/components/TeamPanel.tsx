import React, { useCallback, useEffect, useState } from 'react';
import { X } from 'lucide-react';

import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';
import { parseServerTime } from '../utils/serverTime';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';

interface Workspace {
    id: number;
    name: string;
}

interface PendingInvite {
    id: number;
    email: string;
    expires_at?: string;
}

interface OrgMember {
    user_id: number;
    email: string;
    role: string;
    scope: string;
    workspace_ids: number[];
    can_edit_access: boolean;
}

function describeTeamEvent(ev: any): string {
    const who = ev.subject_email || 'someone';
    const by = ev.actor_email ? ` by ${ev.actor_email}` : '';
    const role = ev.detail?.role ? ` as ${ev.detail.role}` : '';
    switch (ev.event_type) {
        case 'invite_sent':
            // Worth saying out loud: an invite whose email did not leave still
            // happened, and the owner has to relay the link by hand.
            return ev.detail?.email_delivered === false
                ? `Invited ${who}${role}${by}, email not delivered`
                : `Invited ${who}${role}${by}`;
        case 'invite_revoked':
            return `Cancelled the invite to ${who}${by}`;
        case 'invite_accepted':
            return `${who} joined`;
        case 'member_removed':
            return `Removed ${who}${role}${by}`;
        case 'member_access_changed':
            return `Changed access for ${who}${role}${by}`;
        default:
            return `${ev.event_type} ${who}${by}`;
    }
}

function formatEventTime(value?: string | null): string {
    const d = parseServerTime(value);
    if (!d) return '';
    return d.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
}

/**
 * The people in this company, and what each of them can reach.
 *
 * Lifted out of a dialog in WorkspaceSelector, behaviour unchanged. It used to
 * sit behind a button beside the workspace list, which put the company's
 * members behind a control about workspaces, and left Settings with no answer
 * to "who is on my team". One home now; the workspace selector links here.
 *
 * The organization, not the workspace. An invite makes somebody a member of the
 * company, and which workspaces they reach is set per person on the rows below,
 * so naming a workspace at the top promised a scope this panel never had.
 *
 * What the dialog did on open, this does on mount. There is no open event any
 * more: arriving at the section IS the open.
 */
const TeamPanel: React.FC = () => {
    const { user, activeOrganizationId, orgContext } = useUserContext();
    const { addToast } = useToast();
    const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
    // existed. Removed rather than kept warm for a caller that never came.
    const [pendingInvites, setPendingInvites] = useState<PendingInvite[]>([]);
    const [cancellingInviteId, setCancellingInviteId] = useState<number | null>(null);
    // Reach belongs to the organization, not to one workspace, so it is read
    // from the organization endpoint rather than the workspace's member list.
    const [orgMembers, setOrgMembers] = useState<OrgMember[]>([]);
    const [savingAccessFor, setSavingAccessFor] = useState<number | null>(null);
    const [memberToRemove, setMemberToRemove] = useState<OrgMember | null>(null);
    const [membersError, setMembersError] = useState<string | null>(null);
    // What has happened to this team, as opposed to what it looks like now.
    // Removals used to leave no trace anywhere, so somebody missing from the
    // list above had no explanation on any screen or in the database.
    const [teamEvents, setTeamEvents] = useState<any[]>([]);
    const [showTeamHistory, setShowTeamHistory] = useState(false);
    const [inviteEmail, setInviteEmail] = useState('');
    // What the invited person will be when they arrive. Decided here rather
    // than corrected in the list afterwards, which let somebody in with more
    // access than was meant and then asked the owner to go and fix it.
    const [inviteRole, setInviteRole] = useState<'staff' | 'admin'>('staff');
    const [inviteScope, setInviteScope] = useState<'organization' | 'workspace'>('organization');
    const [inviteWorkspaceIds, setInviteWorkspaceIds] = useState<number[]>([]);
    const [isSendingInvite, setIsSendingInvite] = useState(false);
    const [nextSeatCents, setNextSeatCents] = useState<number | null>(null);

    // The workspace list, for the per-person access checkboxes. Fetched here
    // rather than passed in: this panel is reached from Settings now, and
    // Settings has no workspace list of its own to hand over.
    const loadWorkspaces = useCallback(async () => {
        const orgId = activeOrganizationId ?? orgContext?.organization_id ?? null;
        if (!user || !orgId) return;
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/workspaces?organization_id=${orgId}`, {
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (!res.ok) return;
            const payload = await res.json();
            // `items`, not `workspaces`: the route is typed for the shape and
            // not the key. Reading the wrong one shows an owner no workspaces.
            setWorkspaces(payload.items || []);
        } catch (err) {
            console.error('Error fetching workspaces for the team panel:', err);
        }
    }, [user, activeOrganizationId, orgContext]);

    // What the next seat costs, so the price of adding somebody is on screen
    // before the invite goes out rather than on the next invoice.
    const loadNextSeatPrice = useCallback(async () => {
        if (!user) return;
        try {
            const idToken = await user.getIdToken();
            const seatRes = await fetch('/api/v1/subscriptions/seats', {
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (seatRes.ok) {
                const seatData = await seatRes.json();
                setNextSeatCents(
                    typeof seatData?.next_seat_cents === 'number' ? seatData.next_seat_cents : null
                );
            }
        } catch {
            // Leaves the line off. Better silent than a wrong price.
            setNextSeatCents(null);
        }
    }, [user]);

    useEffect(() => {
        loadWorkspaces();
        loadNextSeatPrice();
        fetchOrgMembers();
        fetchTeamEvents();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [user, activeOrganizationId, orgContext?.organization_id]);

    const removeFromOrganization = async (memberUserId: number, email: string) => {
        const orgId = activeOrganizationId ?? orgContext?.organization_id ?? null;
        if (!user || !orgId) return;
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(
                `/api/v1/organizations/${orgId}/members/${memberUserId}`,
                { method: 'DELETE', headers: { Authorization: `Bearer ${idToken}` } },
            );
            if (res.ok) {
                // Names the outcome, not the click. "Removed" alone leaves the
                // owner wondering whether it took effect now or at renewal,
                // which is the question they had when they opened the dialog.
                addToast(`${email} removed. They no longer have access.`, 'success');
                await fetchOrgMembers();
                await fetchTeamEvents();
            } else {
                const data = await res.json().catch(() => ({}));
                addToast(data.detail || 'Could not remove member', 'error');
            }
        } catch {
            addToast('Could not remove member', 'error');
        }
        setMemberToRemove(null);
    };

    const cancelInvite = async (inviteId: number, email: string) => {
        const orgId = activeOrganizationId ?? orgContext?.organization_id ?? null;
        if (!user || !orgId) return;
        setCancellingInviteId(inviteId);
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(
                `/api/v1/organizations/${orgId}/invites/${inviteId}`,
                { method: 'DELETE', headers: { Authorization: `Bearer ${idToken}` } },
            );
            if (res.ok) {
                // Says what happened to the link, because that is the thing the
                // owner is actually worried about still being out there.
                addToast(`Invite to ${email} cancelled. Their link no longer works.`, 'success');
                await fetchOrgMembers();
                await fetchTeamEvents();
            } else {
                const data = await res.json().catch(() => ({}));
                addToast(data.detail || 'Could not cancel the invite', 'error');
            }
        } catch {
            addToast('Could not cancel the invite', 'error');
        }
        setCancellingInviteId(null);
    };

    const fetchTeamEvents = async () => {
        const orgId = localStorage.getItem('active_organization_id');
        if (!orgId || !user) return;
        // Not fatal to the panel, same as the pending-invite list. Failing to
        // show the history must not stop an owner managing the people here.
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/organizations/${orgId}/events`, {
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (res.ok) setTeamEvents((await res.json()).items || []);
        } catch (err) {
            console.error('Error fetching team history:', err);
        }
    };

    const fetchOrgMembers = async () => {
        // activeOrganizationId is set when a tenant is chosen at sign-in, but a
        // reload can reach this before that resolves. orgContext carries the
        // same id and is fetched on its own schedule, so either will do rather
        // than silently returning and leaving the panel blank.
        const orgId = activeOrganizationId ?? orgContext?.organization_id ?? null;
        if (!user || !orgId) {
            setMembersError('Could not work out which organization you are in. Reload and try again.');
            return;
        }
        setMembersError(null);
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/organizations/${orgId}/members`, {
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (res.ok) {
                const data = await res.json();
                setOrgMembers(data.items || []);
            } else {
                const data = await res.json().catch(() => ({}));
                setMembersError(data.detail || 'Could not load the team.');
            }
        } catch (err) {
            console.error('Error fetching organization members:', err);
            setMembersError('Could not load the team.');
        }

        // Who has been invited and not yet arrived. Read from the organization
        // rather than from a workspace: an invite to the company names no
        // workspace, so the workspace listing returned none of them and an
        // invited person appeared on no screen at all until they signed in.
        //
        // Not fatal to the panel. Failing to list who is coming should not stop
        // an owner managing the people already here.
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/organizations/${orgId}/invites`, {
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (res.ok) {
                const data = await res.json();
                setPendingInvites(data.items || []);
            }
        } catch (err) {
            console.error('Error fetching pending invites:', err);
        }
    };


    /** Move somebody between "every workspace" and a chosen set. */
    const updateMemberAccess = async (
        memberUserId: number,
        scope: 'organization' | 'workspace' | null,
        workspaceIds: number[],
        role?: 'member' | 'admin',
    ) => {
        const orgId = activeOrganizationId ?? orgContext?.organization_id ?? null;
        if (!user || !orgId) return;
        setSavingAccessFor(memberUserId);
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(
                `/api/v1/organizations/${orgId}/members/${memberUserId}/access`,
                {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${idToken}` },
                    // Role and access travel together: they are one decision to
                    // the person making it, and sending them separately leaves a
                    // moment where the two disagree.
                    body: JSON.stringify({
                        scope,
                        workspace_ids: workspaceIds,
                        ...(role ? { role } : {}),
                    }),
                },
            );
            if (res.ok) {
                addToast('Access updated', 'success');
                await fetchOrgMembers();
                await fetchTeamEvents();
            } else {
                const data = await res.json().catch(() => ({}));
                addToast(data.detail || 'Could not change access', 'error');
            }
        } catch {
            addToast('Could not change access', 'error');
        } finally {
            setSavingAccessFor(null);
        }
    };

    const handleSendInvite = async () => {
        if (!inviteEmail.trim() || !user || !activeOrganizationId) return;
        setIsSendingInvite(true);
        try {
            const idToken = await user.getIdToken();
            // The invitation carries the decision: what they may do, and how
            // far they can see. Both remain changeable in the list below, but
            // they no longer start as a guess the owner has to correct.
            const orgId = activeOrganizationId ?? orgContext?.organization_id ?? null;
            const url = `/api/v1/organizations/${orgId}/invites`;
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${idToken}` },
                body: JSON.stringify({
                    email: inviteEmail.trim(),
                    role: inviteRole,
                    scope: inviteScope,
                    workspace_ids: inviteScope === 'workspace' ? inviteWorkspaceIds : [],
                }),
            });
            if (res.ok) {
                const data = await res.json().catch(() => ({}));
                // The backend reports whether the mail actually went out. It
                // used to always claim success, so a refused send still read as
                // 'Invite sent' and the invite sat there unmentioned.
                if (data.email_sent === false && data.invite_url) {
                    addToast('Invite created, but the email could not be sent. Link copied below.', 'warning');
                    console.info('Invite link:', data.invite_url);
                } else {
                    addToast(`Invite sent to ${inviteEmail}`, 'success');
                }
                setInviteEmail('');
                setInviteRole('staff');
                setInviteScope('organization');
                setInviteWorkspaceIds([]);
                await fetchOrgMembers();
                await fetchTeamEvents();
            } else {
                const data = await res.json().catch(() => ({}));
                addToast(data.detail || 'Failed to send invite', 'error');
            }
        } catch {
            addToast('Failed to send invite', 'error');
        } finally {
            setIsSendingInvite(false);
        }
    };

    return (
        <div className="team-panel">
            {/* Removing somebody ends their access and stops their seat being
                charged, so it asks first and says what it actually does. */}
            <AlertDialog open={!!memberToRemove} onOpenChange={(open) => { if (!open) setMemberToRemove(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove {memberToRemove?.email}?</AlertDialogTitle>
                    </AlertDialogHeader>
                    <div className="warning-message">
                        <p>They lose access to every workspace in this organization straight away.</p>
                        <p>Their seat stops being charged from today. Nothing they uploaded is deleted.</p>
                    </div>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <Button
                            variant="destructive"
                            onClick={() => memberToRemove && removeFromOrganization(memberToRemove.user_id, memberToRemove.email)}
                        >
                            Remove
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>


                    <div className="form-group" style={{ display: 'flex', gap: 8 }}>
                        <Input
                            type="email"
                            placeholder="colleague@company.com"
                            value={inviteEmail}
                            onChange={e => setInviteEmail(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && handleSendInvite()}
                            disabled={isSendingInvite}
                            style={{ flex: 1 }}
                        />
                        <Button onClick={handleSendInvite} disabled={isSendingInvite || !inviteEmail.trim()}>
                            {isSendingInvite ? 'Sending...' : 'Invite'}
                        </Button>
                    </div>

                    {/* What they will be, chosen before they are let in. */}
                    <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 13, minWidth: 44 }}>Role</span>
                        <Select
                            value={inviteRole}
                            disabled={isSendingInvite}
                            onValueChange={(v) => setInviteRole(v as 'staff' | 'admin')}
                        >
                            <SelectTrigger className="h-8 w-[9rem] text-xs">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="staff">Staff</SelectItem>
                                <SelectItem value="admin">Admin</SelectItem>
                            </SelectContent>
                        </Select>
                        <span style={{ fontSize: 12, color: '#888' }}>
                            {inviteRole === 'admin'
                                ? 'Uploads, deletes, manages people.'
                                : 'Reads and asks questions.'}
                        </span>
                    </div>

                    {/* How far it reaches. Applies to admins too: admin says
                        what they may do, not how much they can see. */}
                    <div style={{ marginTop: 10 }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                            <input
                                type="checkbox"
                                checked={inviteScope === 'organization'}
                                disabled={isSendingInvite}
                                onChange={(e) => {
                                    setInviteScope(e.target.checked ? 'organization' : 'workspace');
                                    setInviteWorkspaceIds(e.target.checked ? [] : workspaces.map(w => w.id));
                                }}
                            />
                            <span>Every workspace, including new ones</span>
                        </label>

                        {inviteScope === 'workspace' && (
                            <div style={{ paddingLeft: 22, marginTop: 4 }}>
                                {workspaces.map(ws => {
                                    const checked = inviteWorkspaceIds.includes(ws.id);
                                    return (
                                        <label
                                            key={ws.id}
                                            style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', padding: '2px 0' }}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={checked}
                                                disabled={isSendingInvite}
                                                onChange={() => setInviteWorkspaceIds(
                                                    checked
                                                        ? inviteWorkspaceIds.filter(id => id !== ws.id)
                                                        : [...inviteWorkspaceIds, ws.id],
                                                )}
                                            />
                                            <span>{ws.name}</span>
                                        </label>
                                    );
                                })}
                            </div>
                        )}
                    </div>

                    <p className="text-xs text-muted-foreground mt-2">
                        You can change both from the list below at any time.
                    </p>

                    {nextSeatCents !== null && (
                        <p className="text-xs text-muted-foreground mt-2">
                            {nextSeatCents > 0
                                ? `Adding this person costs $${(nextSeatCents / 100).toFixed(0)}/month.`
                                : 'This person fits within your included seats, at no extra cost.'}
                        </p>
                    )}

                    {/* Current members, with what each of them can see.
                        Reach used to be fixed at invite time, so moving
                        somebody between workspaces meant removing and
                        re-inviting them, which churned the seat count for a
                        change that costs nothing. */}
                    {(
                        <div style={{ marginTop: 20 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', color: '#888', marginBottom: 8 }}>Members</div>
                            {membersError && (
                                <div className="error-message" style={{ fontSize: 13, marginBottom: 8 }}>{membersError}</div>
                            )}
                            {!membersError && orgMembers.length === 0 && (
                                <div style={{ fontSize: 13, color: '#888' }}>
                                    Nobody else yet. Invite someone above.
                                </div>
                            )}
                            {orgMembers.map(m => {
                                const isSaving = savingAccessFor === m.user_id;
                                // One workspace selected, or every workspace.
                                // A multi-select belongs here eventually; until
                                // then this covers the two reaches the backend
                                // actually distinguishes.
                                const value = m.scope === 'organization'
                                    ? 'organization'
                                    : (m.workspace_ids[0] != null ? String(m.workspace_ids[0]) : 'none');
                                return (
                                    <div key={m.user_id} style={{ padding: '10px 0', borderBottom: '1px solid rgba(128,128,128,0.15)' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                                            <span style={{ fontSize: 14 }}>{m.email}</span>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                                {m.role === 'owner' ? (
                                                    <span style={{ fontSize: 12, color: '#888' }}>Owner</span>
                                                ) : (
                                                    /* What they may do, next to what they may
                                                       see, because an owner deciding one is
                                                       usually deciding both. */
                                                    <Select
                                                        // Anything that is not admin reads as staff,
                                                        // so a row still carrying the old word does
                                                        // not render an empty control.
                                                        value={m.role === 'admin' ? 'admin' : 'staff'}
                                                        disabled={isSaving}
                                                        onValueChange={(v) =>
                                                            updateMemberAccess(
                                                                m.user_id,
                                                                null,
                                                                [],
                                                                v as 'member' | 'admin',
                                                            )
                                                        }
                                                    >
                                                        <SelectTrigger className="h-7 w-[8.5rem] text-xs">
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {/* The role by name, with what it
                                                                means underneath. Naming the
                                                                effect instead ("Can read only")
                                                                gave one role a different name on
                                                                every screen, and none of them
                                                                matched the word support, the
                                                                database and the docs use. */}
                                                            <SelectItem value="staff">Staff</SelectItem>
                                                            <SelectItem value="admin">Admin</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                )}
                                                {m.role !== 'owner' && (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon-sm"
                                                        onClick={() => setMemberToRemove(m)}
                                                        title="Remove from the organization"
                                                    >
                                                        <X className="size-3.5" />
                                                    </Button>
                                                )}
                                            </div>
                                        </div>

                                        {m.can_edit_access ? (
                                            <div style={{ marginTop: 8 }}>
                                                {/* Access is a staff question and only a staff
                                                    question. An admin runs the company, so they
                                                    see all of it whatever this said, which is
                                                    why the backend reports can_edit_access false
                                                    for them and this whole block is absent.
                                                    Somebody who should be held to two
                                                    workspaces is staff. */}
                                                {/* Describes this row, not the branch it sits in.
                                                    This said "Reads and asks questions" for
                                                    everybody, because only staff could reach here
                                                    when it was written. Admins can be scoped now,
                                                    so an admin was told they change nothing while
                                                    holding the controls that prove otherwise. */}
                                                <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>
                                                    {m.role === 'admin'
                                                        ? 'Uploads, deletes, and manages people, in the workspaces below.'
                                                        : 'Reads and asks questions. Changes nothing.'}
                                                </div>
                                                {/* Said plainly rather than left
                                                    to be inferred from a column
                                                    of empty boxes. This is the
                                                    person the panel exists to
                                                    act on, and they used to be
                                                    the one it hid. */}
                                                {m.scope !== 'organization' && m.workspace_ids.length === 0 && (
                                                    <div style={{ fontSize: 12, color: '#b45309', marginBottom: 6 }}>
                                                        No workspace yet, so they can see no documents. Tick one below.
                                                    </div>
                                                )}
                                                {/* Checkboxes, not one-of-many: access is a
                                                    set. Somebody can belong to three of five
                                                    workspaces, which a single select cannot
                                                    express. "Every workspace" is the state
                                                    where all are ticked, and it keeps
                                                    including workspaces created later. */}
                                                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 4 }}>
                                                    <input
                                                        type="checkbox"
                                                        checked={m.scope === 'organization'}
                                                        disabled={isSaving}
                                                        onChange={(e) =>
                                                            e.target.checked
                                                                ? updateMemberAccess(m.user_id, 'organization', [])
                                                                : updateMemberAccess(m.user_id, 'workspace', workspaces.map(w => w.id))
                                                        }
                                                    />
                                                    <span style={{ fontWeight: 500 }}>
                                                        Every workspace, including new ones
                                                    </span>
                                                </label>

                                                {m.scope !== 'organization' && (
                                                    <div style={{ paddingLeft: 22 }}>
                                                        {workspaces.map(ws => {
                                                            const checked = m.workspace_ids.includes(ws.id);
                                                            return (
                                                                <label
                                                                    key={ws.id}
                                                                    style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', padding: '2px 0' }}
                                                                >
                                                                    <input
                                                                        type="checkbox"
                                                                        checked={checked}
                                                                        disabled={isSaving}
                                                                        onChange={() => {
                                                                            const next = checked
                                                                                ? m.workspace_ids.filter(id => id !== ws.id)
                                                                                : [...m.workspace_ids, ws.id];
                                                                            if (next.length === 0) {
                                                                                addToast(
                                                                                    'Keep at least one workspace, or remove them from the organization.',
                                                                                    'warning',
                                                                                );
                                                                                return;
                                                                            }
                                                                            updateMemberAccess(m.user_id, 'workspace', next);
                                                                        }}
                                                                    />
                                                                    <span>{ws.name}</span>
                                                                </label>
                                                            );
                                                        })}
                                                    </div>
                                                )}
                                            </div>
                                        ) : (
                                            <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                                                {/* Says why there is no workspace picker here,
                                                    rather than leaving a control that appears
                                                    for one person and not another with no
                                                    explanation. */}
                                                {m.role === 'owner'
                                                    ? 'Sees every workspace. Manages billing.'
                                                    : 'Sees every workspace. Uploads, deletes, and manages people.'}
                                            </div>
                                        )}
                                    </div>
                                );
                            })}

                        </div>
                    )}

                    {/* Pending invites */}
                    {pendingInvites.length > 0 && (
                        <div style={{ marginTop: 20 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', color: '#888', marginBottom: 8 }}>Pending Invites</div>
                            {pendingInvites.map(inv => (
                                <div key={inv.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(128,128,128,0.15)' }}>
                                    <span style={{ fontSize: 14 }}>{inv.email}</span>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                        <span style={{ fontSize: 12, color: '#888' }}>
                                            {cancellingInviteId === inv.id ? 'Cancelling...' : 'Pending'}
                                        </span>
                                        <Button
                                            variant="ghost"
                                            size="icon-sm"
                                            disabled={cancellingInviteId === inv.id}
                                            onClick={() => cancelInvite(inv.id, inv.email)}
                                            title="Cancel this invite"
                                        >
                                            <X className="size-3.5" />
                                        </Button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* History.
                        Collapsed, because the common visit to this panel is to
                        invite somebody and the list above is the answer to that.
                        It is here rather than in Settings because the question
                        it answers, "why is this person not in the list", is
                        asked while looking at the list. */}
                    {teamEvents.length > 0 && (
                        <div style={{ marginTop: 20 }}>
                            <button
                                type="button"
                                onClick={() => setShowTeamHistory(v => !v)}
                                style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', color: '#888', background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
                            >
                                History {showTeamHistory ? '▾' : '▸'}
                            </button>
                            {showTeamHistory && (
                                <div style={{ marginTop: 8, maxHeight: 220, overflowY: 'auto' }}>
                                    {teamEvents.map(ev => (
                                        <div key={ev.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '6px 0', borderBottom: '1px solid rgba(128,128,128,0.12)' }}>
                                            <span style={{ fontSize: 13 }}>{describeTeamEvent(ev)}</span>
                                            <span style={{ fontSize: 12, color: '#888', whiteSpace: 'nowrap' }}>
                                                {formatEventTime(ev.created_at)}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

        </div>
    );
};

export default TeamPanel;
