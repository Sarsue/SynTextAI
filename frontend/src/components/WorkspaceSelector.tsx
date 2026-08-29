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
    /** How many documents deleting this workspace would destroy. */
    document_count?: number;
    created_at: string;
    updated_at: string;
}

/** A member of the organization, with how far their access reaches. */
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

    // THIS PANEL IS ABOUT THE COMPANY, NOT ABOUT ONE WORKSPACE
    //
    // It used to be titled "Team — <workspace>" and filtered to whoever could
    // already see that workspace. The filter was added for a real reason:
    // listing everybody under Payroll made somebody confined to Finance look
    // like they could read it, which an owner reasonably read as a leak.
    //
    // But hiding them fixed the wrong half. The row is also where a workspace
    // is granted, so filtering by who already has access hid the control from
    // exactly the people who needed it, and anybody assigned to no workspace
    // at all appeared on no screen anywhere and could never be assigned one.
    // There was no workspace to switch to, which is what the old "switch
    // workspace to manage them" note was asking for.
    //
    // So: everyone in the company, every time, and each row states its own
    // reach and carries the checkboxes that change it. Nobody is hidden and
    // nobody appears somewhere they cannot go, because the row says where they
    // can go rather than the list implying it.

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

    /** Remove somebody from the company, not just from one workspace. */

    /** Take back an invite that has not been accepted yet.

     * No confirmation step, unlike removing a member. Nobody loses access here
     * because nobody has any yet, and the whole action is undone by inviting
     * the same address again. */





    // handleRemoveMember was here: a DELETE to the workspace's member list, with
    // no caller. The X on a row removes somebody from the COMPANY, which is the
    // decision an owner is actually making, and taking a workspace away is
    // unticking it. Removing from one workspace while leaving them in the
    // company is the same operation as unticking that box, and having two ways
    // to do it is how they drift.

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
                            /* One home for the team, in Settings. This used to
                               open a dialog here, which put the company's people
                               behind a control about workspaces. */
                            onClick={() => { window.location.hash = '#/settings/team'; }}
                            title="Manage the team"
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
                            {/* The count, not "all files". Deleting a workspace
                                destroys every document in it and the stored
                                objects behind them, and nothing here said how
                                many, so the warning read the same whether the
                                workspace held nothing or held everything the
                                company had uploaded. */}
                            <li>
                                {typeof workspaceToEdit?.document_count === 'number'
                                    ? workspaceToEdit.document_count === 0
                                        ? 'No documents (this workspace is empty)'
                                        : workspaceToEdit.document_count === 1
                                            ? '1 document'
                                            : `${workspaceToEdit.document_count} documents`
                                    : 'All files in this workspace'}
                            </li>
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
