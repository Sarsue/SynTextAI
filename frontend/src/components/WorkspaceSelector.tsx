import React, { useState, useEffect } from 'react';
import { Folder, Users, Plus, ChevronDown, Pencil, Trash2, Check, Package, AlertTriangle, X } from 'lucide-react';
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

interface Workspace {
    id: number;
    name: string;
    user_id: number;
    role: string;
    created_at: string;
    updated_at: string;
}

interface Member {
    user_id: number;
    email: string;
    role: string;
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
    const { user, subscriptionStatus, setCurrentWorkspaceRole } = useUserContext();
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
    const [inviteEmail, setInviteEmail] = useState('');
    const [isSendingInvite, setIsSendingInvite] = useState(false);

    // Backend entitlement rules:
    // - premium: active | trialing
    // - free: none (or missing)
    const normalizedStatus = (subscriptionStatus || 'none').toLowerCase();
    // Keyed to this user's OWN subscription on purpose. Creating a workspace
    // makes you its owner and therefore the account billed for it, so inherited
    // entitlement from someone else's workspace must not unlock it.
    const isFreeUser = normalizedStatus === 'none';
    const isOwner = currentWorkspace?.role === 'owner';

    // The free workspace limit applies to workspaces you *own*. The backend's
    // count_workspaces_for_user counts only owned rows, so counting every
    // workspace here told staff to upgrade for something the backend allows.
    const ownedWorkspaceCount = workspaces.filter(w => w.role === 'owner').length;

    // Fetch workspaces on mount
    useEffect(() => {
        if (user) {
            fetchWorkspaces();
        }
    }, [user]);

    const fetchWorkspaces = async (preferredWorkspaceId?: number) => {
        if (!user) return;

        try {
            const idToken = await user.getIdToken();
            const response = await fetch('/api/v1/workspaces', {
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

        // Check free tier limit
        if (isFreeUser && ownedWorkspaceCount >= 1) {
            addToast('Free users are limited to 1 workspace. Upgrade to create more!', 'error');
            setShowCreateModal(false);
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

    const handleOpenMembers = () => {
        if (currentWorkspace) {
            fetchMembers(currentWorkspace.id);
            setShowMembersModal(true);
            setShowDropdown(false);
        }
    };

    const handleSendInvite = async () => {
        if (!inviteEmail.trim() || !currentWorkspace || !user) return;
        setIsSendingInvite(true);
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/workspaces/${currentWorkspace.id}/invites`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${idToken}` },
                body: JSON.stringify({ email: inviteEmail.trim() }),
            });
            if (res.ok) {
                addToast(`Invite sent to ${inviteEmail}`, 'success');
                setInviteEmail('');
                await fetchMembers(currentWorkspace.id);
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

                    {isOwner && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleOpenMembers}
                            title="Manage team members"
                        >
                            <Users className="size-4" /> Team
                        </Button>
                    )}
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setShowCreateModal(true)}
                        title={isFreeUser && ownedWorkspaceCount >= 1 ? 'Upgrade to create more workspaces' : 'Create new workspace'}
                    >
                        <Plus className="size-4" /> New
                    </Button>
                </div>

                {isFreeUser && ownedWorkspaceCount >= 1 && (
                    <div className="workspace-limit-banner">
                        <Package className="size-4 shrink-0" />
                        <span>Free plan: 1 workspace. <a href="/settings">Upgrade</a> for more!</span>
                    </div>
                )}
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

                    {isFreeUser && ownedWorkspaceCount >= 1 && (
                        <div className="upgrade-prompt">
                            <p><strong>Free Tier Limit Reached</strong></p>
                            <p>Upgrade to premium to create unlimited workspaces and unlock more features!</p>
                            <a href="/settings" className="upgrade-link">View Plans →</a>
                        </div>
                    )}

                    <DialogFooter>
                        <Button variant="outline" onClick={handleCancelCreate} disabled={isCreating}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleCreateWorkspace}
                            disabled={isCreating || !newWorkspaceName.trim() || (isFreeUser && ownedWorkspaceCount >= 1)}
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

                    {/* Current members */}
                    {members.length > 0 && (
                        <div style={{ marginTop: 20 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', color: '#888', marginBottom: 8 }}>Members</div>
                            {members.map(m => (
                                <div key={m.user_id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid rgba(128,128,128,0.15)' }}>
                                    <span style={{ fontSize: 14 }}>{m.email}</span>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                        <span style={{ fontSize: 12, color: '#888', textTransform: 'capitalize' }}>{m.role}</span>
                                        {m.role !== 'owner' && (
                                            <Button
                                                variant="ghost"
                                                size="icon-sm"
                                                onClick={() => handleRemoveMember(m.user_id)}
                                                title="Remove member"
                                            >
                                                <X className="size-3.5" />
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            ))}
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
                            <li>All associated key concepts, flashcards, and quizzes</li>
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
