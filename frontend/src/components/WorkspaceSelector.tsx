import React, { useState, useEffect } from 'react';
import { Folder, Users, Plus, ChevronDown, Pencil, Trash2, Check, AlertTriangle, X } from 'lucide-react';
import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';
import './WorkspaceSelector.css';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog';
import {
    AlertDialog,
    AlertDialogContent,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogFooter,
    AlertDialogCancel,
} from '@/components/ui/alert-dialog';
import {
    DropdownMenu,
    DropdownMenuTrigger,
    DropdownMenuContent,
} from '@/components/ui/dropdown-menu';
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
    user_id: number;
    // What you are here: owner, admin or staff. Distinct from can_manage,
    // which is what you may do. The backend used to report "owner" for admins
    // because every caller wanted the second question, which left anything
    // asking the first one quietly wrong.
    role: string;
    can_manage: boolean;
    created_at: string;
    updated_at: string;
}

interface Member {
    user_id: number;
    email: string;
    role: string;
}

/** A member of the organization, with how far their access reaches. */
interface OrgMember {
    user_id: number;
    email: string;
    role: string;
    scope: 'organization' | 'workspace';
    workspace_ids: number[];
    can_edit_access: boolean;
}

interface PendingInvite {
    id: number;
    email: string;
    expires_at: string;
}

interface WorkspaceSelectorProps {
    darkMode?: boolean;
    onWorkspaceChange?: (workspaceId: number) => void;
}

const WorkspaceSelector: React.FC<WorkspaceSelectorProps> = ({ darkMode = false, onWorkspaceChange }) => {
    const { user, setCurrentWorkspaceRole, activeOrganizationId, orgContext, accessChangedAt } = useUserContext();
    const { addToast } = useToast();

    const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
    const [currentWorkspace, setCurrentWorkspace] = useState<Workspace | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [showCreateModal, setShowCreateModal] = useState(false);
    const [showDropdown, setShowDropdown] = useState(false);
    const [showRenameModal, setShowRenameModal] = useState(false);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [workspaceToEdit, setWorkspaceToEdit] = useState<Workspace | null>(null);
    const [newWorkspaceName, setNewWorkspaceName] = useState('');
    const [renameWorkspaceName, setRenameWorkspaceName] = useState('');
    const [isCreating, setIsCreating] = useState(false);
    const [isRenaming, setIsRenaming] = useState(false);
    const [isDeleting, setIsDeleting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Invite / members state
    const [showMembersModal, setShowMembersModal] = useState(false);
    const [members, setMembers] = useState<Member[]>([]);
    const [pendingInvites, setPendingInvites] = useState<PendingInvite[]>([]);
    // Reach belongs to the organization, not to one workspace, so it is read
    // from the organization endpoint rather than the workspace's member list.
    const [orgMembers, setOrgMembers] = useState<OrgMember[]>([]);
    const [savingAccessFor, setSavingAccessFor] = useState<number | null>(null);
    const [memberToRemove, setMemberToRemove] = useState<OrgMember | null>(null);
    const [membersError, setMembersError] = useState<string | null>(null);
    const [inviteEmail, setInviteEmail] = useState('');
    // What the invited person will be when they arrive. Decided here rather
    // than corrected in the list afterwards, which let somebody in with more
    // access than was meant and then asked the owner to go and fix it.
    const [inviteRole, setInviteRole] = useState<'staff' | 'admin'>('staff');
    const [inviteScope, setInviteScope] = useState<'organization' | 'workspace'>('organization');
    const [inviteWorkspaceIds, setInviteWorkspaceIds] = useState<number[]>([]);
    const [isSendingInvite, setIsSendingInvite] = useState(false);
    const [nextSeatCents, setNextSeatCents] = useState<number | null>(null);

    // There is no free plan, so there is nothing to count here.
    //
    // This screen used to carry a whole tier: a workspace allowance, an upgrade
    // banner, a create button that disabled itself past one workspace. All of
    // it described a product that was cancelled before launch. Signing up takes
    // a card and a demo is arranged by adding somebody to a company that
    // already pays, so an organization is subscribed or it cannot reach this
    // screen at all — which made the banner something only a paying customer
    // could ever be shown, and only when their status failed to load.
    //
    // Whether the button appears is one question with one answer, and the
    // backend gives it. Default true so a missing field cannot lock an owner
    // out of their own product; the backend refuses regardless, so the worst
    // case is a button that explains itself when pressed.
    const canCreateWorkspace = orgContext?.can_create_workspace ?? true;
    // Managing the team is an admin's job as much as an owner's, so ask
    // whether you may manage rather than whether you are the owner.
    const canManageWorkspace = currentWorkspace?.can_manage ?? false;

    // Fetch workspaces on mount, and again whenever access changes.
    //
    // accessChangedAt is bumped by the socket event an owner's change sends, so
    // a workspace granted or revoked appears or disappears here without the
    // person having to reload — which they had no reason to do, and which for a
    // revocation left them looking at something they no longer had.
    useEffect(() => {
        if (user) {
            fetchWorkspaces();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [user, accessChangedAt]);

    const fetchWorkspaces = async (preferredWorkspaceId?: number) => {
        if (!user) return;

        try {
            const idToken = await user.getIdToken();
            // Scope to the organization the user chose, so two companies'
            // workspaces never appear side by side with no boundary.
            const url = activeOrganizationId
                ? `/api/v1/workspaces?organization_id=${activeOrganizationId}`
                : '/api/v1/workspaces';
            const response = await fetch(url, {
                headers: {
                    'Authorization': `Bearer ${idToken}`,
                },
            });

            if (!response.ok) {
                throw new Error('Failed to fetch workspaces');
            }

            const data = await response.json();
            const workspaceList: Workspace[] = (data.items || []).filter(
                (ws: any): ws is Workspace => ws && typeof ws.id === 'number'
            );
            setWorkspaces(workspaceList);

            // Set current workspace
            if (workspaceList.length > 0) {
                const selectedWorkspace =
                    (preferredWorkspaceId
                        ? workspaceList.find((ws: Workspace) => ws.id === preferredWorkspaceId)
                        : null) || workspaceList[0];

                if (selectedWorkspace) {
                    setCurrentWorkspace(selectedWorkspace);
                    setCurrentWorkspaceRole(selectedWorkspace.role);
                    if (onWorkspaceChange) {
                        onWorkspaceChange(selectedWorkspace.id);
                    }
                } else {
                    setCurrentWorkspace(null);
                    setCurrentWorkspaceRole(null);
                }
            } else {
                setCurrentWorkspace(null);
                setCurrentWorkspaceRole(null);
            }

            setIsLoading(false);
        } catch (err) {
            console.error('Error fetching workspaces:', err);
            setError('Failed to load workspaces');
            setIsLoading(false);
        }
    };

    const handleCreateWorkspace = async () => {
        if (!newWorkspaceName.trim()) {
            addToast('Please enter a workspace name', 'error');
            return;
        }

        if (!user) {
            addToast('You must be logged in to create a workspace', 'error');
            return;
        }

        setIsCreating(true);
        setError(null);

        try {
            const idToken = await user.getIdToken();
            const response = await fetch('/api/v1/workspaces', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
                body: JSON.stringify({ name: newWorkspaceName.trim() }),
            });

            const rawText = await response.text();
            let data: any = null;
            if (rawText) {
                try {
                    data = JSON.parse(rawText);
                } catch {
                    data = { message: rawText };
                }
            }

            if (!response.ok) {
                // Handle specific error codes
                const errorCode = data?.detail?.error_code || data?.error_code;
                if (response.status === 402 && errorCode === 'WORKSPACE_LIMIT_REACHED') {
                    addToast('Workspace limit reached. Upgrade to premium to create more workspaces!', 'error');
                    setShowCreateModal(false);
                    return;
                }

                const errorMessage =
                    (typeof data?.detail === 'string' ? data.detail : data?.detail?.message) ||
                    data?.message ||
                    'Failed to create workspace';
                throw new Error(errorMessage);
            }

            // Success
            const newWorkspace = (data as any)?.data ?? data;
            const createdWorkspaceId =
                typeof (newWorkspace as any)?.id === 'number'
                    ? (newWorkspace as any).id
                    : typeof (data as any)?.id === 'number'
                        ? (data as any).id
                        : undefined;
            addToast('Workspace created successfully!', 'success');
            setNewWorkspaceName('');
            setShowCreateModal(false);

            // Refresh workspaces list and switch to new workspace
            await fetchWorkspaces(createdWorkspaceId);
        } catch (err) {
            console.error('Error creating workspace:', err);
            const errorMsg = err instanceof Error ? err.message : 'Failed to create workspace';
            setError(errorMsg);
            addToast(errorMsg, 'error');
        } finally {
            setIsCreating(false);
        }
    };

    const handleCancelCreate = () => {
        setShowCreateModal(false);
        setNewWorkspaceName('');
        setError(null);
    };

    const handleWorkspaceSelect = (workspace: Workspace) => {
        setCurrentWorkspace(workspace);
        setCurrentWorkspaceRole(workspace.role);
        setShowDropdown(false);
        if (onWorkspaceChange) {
            onWorkspaceChange(workspace.id);
        }
    };

    // Rename workspace
    const handleRenameClick = (workspace: Workspace, e: React.MouseEvent) => {
        e.stopPropagation();
        setWorkspaceToEdit(workspace);
        setRenameWorkspaceName(workspace.name);
        setShowRenameModal(true);
        setShowDropdown(false);
    };

    const handleRenameWorkspace = async () => {
        if (!workspaceToEdit || !user) return;
        if (renameWorkspaceName.trim().length === 0) {
            setError('Workspace name cannot be empty');
            return;
        }

        setIsRenaming(true);
        setError(null);

        try {
            const idToken = await user.getIdToken();
            const response = await fetch(`/api/v1/workspaces/${workspaceToEdit.id}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
                body: JSON.stringify({ name: renameWorkspaceName }),
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Failed to rename workspace');
            }

            const renamedWorkspaceId = workspaceToEdit.id;
            addToast('Workspace renamed successfully!', 'success');
            setShowRenameModal(false);
            setRenameWorkspaceName('');
            setWorkspaceToEdit(null);
            await fetchWorkspaces(renamedWorkspaceId);
        } catch (err) {
            console.error('Error renaming workspace:', err);
            const errorMsg = err instanceof Error ? err.message : 'Failed to rename workspace';
            setError(errorMsg);
            addToast(errorMsg, 'error');
        } finally {
            setIsRenaming(false);
        }
    };

    // Delete workspace
    const handleDeleteClick = (workspace: Workspace, e: React.MouseEvent) => {
        e.stopPropagation();
        setWorkspaceToEdit(workspace);
        setShowDeleteModal(true);
        setShowDropdown(false);
    };

    const handleDeleteWorkspace = async () => {
        if (!workspaceToEdit || !user) return;

        setIsDeleting(true);
        setError(null);

        try {
            const idToken = await user.getIdToken();
            const response = await fetch(`/api/v1/workspaces/${workspaceToEdit.id}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${idToken}`,
                },
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Failed to delete workspace');
            }

            addToast('Workspace deleted successfully!', 'success');
            setShowDeleteModal(false);
            setWorkspaceToEdit(null);

            // Fetch updated workspace list
            await fetchWorkspaces();

            // If deleted current workspace, switch to first available
            if (currentWorkspace?.id === workspaceToEdit.id) {
                const updatedWorkspaces = workspaces.filter(ws => ws.id !== workspaceToEdit.id);
                if (updatedWorkspaces.length > 0 && onWorkspaceChange) {
                    onWorkspaceChange(updatedWorkspaces[0].id);
                }
            }
        } catch (err) {
            console.error('Error deleting workspace:', err);
            const errorMsg = err instanceof Error ? err.message : 'Failed to delete workspace';
            setError(errorMsg);
            addToast(errorMsg, 'error');
        } finally {
            setIsDeleting(false);
        }
    };

    const handleCancelRename = () => {
        setShowRenameModal(false);
        setRenameWorkspaceName('');
        setWorkspaceToEdit(null);
        setError(null);
    };

    const handleCancelDelete = () => {
        setShowDeleteModal(false);
        setWorkspaceToEdit(null);
        setError(null);
    };

    const fetchMembers = async (wsId: number) => {
        if (!user) return;
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/workspaces/${wsId}/members`, {
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (res.ok) {
                const data = await res.json();
                setMembers(data.members || []);
                setPendingInvites(data.pending_invites || []);
            }
        } catch (err) {
            console.error('Error fetching members:', err);
        }
    };

    /** Remove somebody from the company, not just from one workspace. */
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
                if (currentWorkspace) await fetchMembers(currentWorkspace.id);
            } else {
                const data = await res.json().catch(() => ({}));
                addToast(data.detail || 'Could not remove member', 'error');
            }
        } catch {
            addToast('Could not remove member', 'error');
        }
        setMemberToRemove(null);
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
                if (currentWorkspace) await fetchMembers(currentWorkspace.id);
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

    const handleOpenMembers = async () => {
        if (currentWorkspace) {
            fetchMembers(currentWorkspace.id);
            fetchOrgMembers();
            setShowMembersModal(true);
            setShowDropdown(false);
            // What the next seat costs, so the price of adding somebody is on
            // screen before the invite goes out rather than on the next invoice.
            try {
                const idToken = await user!.getIdToken();
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
                if (currentWorkspace) await fetchMembers(currentWorkspace.id);
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

    const handleRemoveMember = async (targetUserId: number) => {
        if (!currentWorkspace || !user) return;
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/workspaces/${currentWorkspace.id}/members/${targetUserId}`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (res.ok) {
                addToast('Member removed', 'success');
                await fetchMembers(currentWorkspace.id);
            } else {
                addToast('Failed to remove member', 'error');
            }
        } catch {
            addToast('Failed to remove member', 'error');
        }
    };

    if (isLoading) {
        return (
            <div className={`workspace-selector ${darkMode ? 'dark-mode' : ''}`}>
                <div className="workspace-loading">Loading workspaces...</div>
            </div>
        );
    }

    return (
        <>
            <div className={`workspace-selector ${darkMode ? 'dark-mode' : ''}`}>
                <div className="workspace-header">
                    <DropdownMenu open={showDropdown} onOpenChange={setShowDropdown}>
                        <DropdownMenuTrigger asChild>
                            <button className="workspace-info">
                                <Folder className="size-4 shrink-0" />
                                <div className="workspace-details">
                                    <span className="workspace-label">Workspace</span>
                                    <span className="workspace-name">
                                        {currentWorkspace?.name || 'My Workspace'}
                                    </span>
                                </div>
                                <ChevronDown className={`size-4 shrink-0 transition-transform ${showDropdown ? 'rotate-180' : ''}`} />
                            </button>
                        </DropdownMenuTrigger>
                        {workspaces.length > 1 && (
                            <DropdownMenuContent className="w-64">
                                {workspaces.map((workspace) => (
                                    <div key={workspace.id} className="workspace-item-wrapper">
                                        <button
                                            className={`workspace-item ${currentWorkspace?.id === workspace.id ? 'active' : ''}`}
                                            onClick={() => handleWorkspaceSelect(workspace)}
                                        >
                                            <Folder className="size-4 shrink-0 workspace-item-icon" />
                                            <span className="workspace-item-name">{workspace.name}</span>
                                            {currentWorkspace?.id === workspace.id && (
                                                <Check className="size-4 shrink-0" />
                                            )}
                                        </button>
                                        <div className="workspace-item-actions">
                                            <Button
                                                variant="ghost"
                                                size="icon-sm"
                                                onClick={(e) => handleRenameClick(workspace, e)}
                                                title="Rename workspace"
                                            >
                                                <Pencil className="size-3.5" />
                                            </Button>
                                            {workspaces.length > 1 && (
                                                <Button
                                                    variant="ghost"
                                                    size="icon-sm"
                                                    onClick={(e) => handleDeleteClick(workspace, e)}
                                                    title="Delete workspace"
                                                >
                                                    <Trash2 className="size-3.5" />
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </DropdownMenuContent>
                        )}
                    </DropdownMenu>

                    {canManageWorkspace && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleOpenMembers}
                            title="Manage team members"
                        >
                            <Users className="size-4" /> Team
                        </Button>
                    )}
                    {/* Hidden for a read-only member, who the backend refuses.
                        Shown from the capability the backend reports, rather
                        than re-deriving the role rules here, so the button and
                        the refusal cannot disagree. */}
                    {canCreateWorkspace && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setShowCreateModal(true)}
                            title="Create new workspace"
                        >
                            <Plus className="size-4" /> New
                        </Button>
                    )}
                </div>
            </div>

            {/* Create Workspace Modal */}
            <Dialog open={showCreateModal} onOpenChange={(open) => !open && handleCancelCreate()}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Create New Workspace</DialogTitle>
                    </DialogHeader>

                    <div className="form-group">
                        <label htmlFor="workspace-name">Workspace Name</label>
                        <Input
                            id="workspace-name"
                            type="text"
                            value={newWorkspaceName}
                            onChange={(e) => setNewWorkspaceName(e.target.value)}
                            placeholder="e.g., Personal Projects, Study Materials..."
                            maxLength={100}
                            autoFocus
                            disabled={isCreating}
                        />
                        <span className="input-hint">
                            Choose a descriptive name for organizing your files
                        </span>
                    </div>

                    {error && <div className="error-message">{error}</div>}

                    <DialogFooter>
                        <Button variant="outline" onClick={handleCancelCreate} disabled={isCreating}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleCreateWorkspace}
                            disabled={isCreating || !newWorkspaceName.trim()}
                        >
                            {isCreating ? 'Creating...' : 'Create Workspace'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Rename Workspace Modal */}
            <Dialog open={showRenameModal && !!workspaceToEdit} onOpenChange={(open) => !open && handleCancelRename()}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Rename Workspace</DialogTitle>
                    </DialogHeader>

                    <div className="form-group">
                        <label htmlFor="rename-workspace-name">New Name</label>
                        <Input
                            id="rename-workspace-name"
                            type="text"
                            value={renameWorkspaceName}
                            onChange={(e) => setRenameWorkspaceName(e.target.value)}
                            placeholder="Enter new workspace name..."
                            maxLength={100}
                            autoFocus
                            disabled={isRenaming}
                        />
                    </div>

                    {error && <div className="error-message">{error}</div>}

                    <DialogFooter>
                        <Button variant="outline" onClick={handleCancelRename} disabled={isRenaming}>
                            Cancel
                        </Button>
                        <Button onClick={handleRenameWorkspace} disabled={isRenaming || !renameWorkspaceName.trim()}>
                            {isRenaming ? 'Renaming...' : 'Rename'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

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

            {/* Members / Invite Modal */}
            <Dialog open={showMembersModal} onOpenChange={setShowMembersModal}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Team — {currentWorkspace?.name}</DialogTitle>
                    </DialogHeader>

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
                                                <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>
                                                    Reads and asks questions. Changes nothing.
                                                </div>
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
                                <div key={inv.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid rgba(128,128,128,0.15)' }}>
                                    <span style={{ fontSize: 14 }}>{inv.email}</span>
                                    <span style={{ fontSize: 12, color: '#888' }}>Pending</span>
                                </div>
                            ))}
                        </div>
                    )}

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowMembersModal(false)}>Close</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete Workspace Confirmation */}
            <AlertDialog open={showDeleteModal && !!workspaceToEdit} onOpenChange={(open) => !open && handleCancelDelete()}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle className="flex items-center gap-2">
                            <AlertTriangle className="size-4 text-destructive" /> Delete Workspace
                        </AlertDialogTitle>
                    </AlertDialogHeader>

                    <div className="warning-message">
                        <p><strong>Are you sure you want to delete "{workspaceToEdit?.name}"?</strong></p>
                        <p>This will permanently delete:</p>
                        <ul>
                            <li>All files in this workspace</li>
                            <li>Everything extracted from those files for search</li>
                            <li>All chat history related to these files</li>
                        </ul>
                        <p><strong>This action cannot be undone.</strong></p>
                    </div>

                    {error && <div className="error-message">{error}</div>}

                    <AlertDialogFooter>
                        <AlertDialogCancel onClick={handleCancelDelete} disabled={isDeleting}>
                            Cancel
                        </AlertDialogCancel>
                        <Button variant="destructive" onClick={handleDeleteWorkspace} disabled={isDeleting}>
                            {isDeleting ? 'Deleting...' : 'Delete Workspace'}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
};

export default WorkspaceSelector;
