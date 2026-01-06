import React, { useState, useEffect } from 'react';
import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';
import './WorkspaceSelector.css';

interface Workspace {
    id: number;
    name: string;
    user_id: number;
    created_at: string;
    updated_at: string;
}

interface WorkspaceSelectorProps {
    darkMode?: boolean;
    onWorkspaceChange?: (workspaceId: number) => void;
}

const WorkspaceSelector: React.FC<WorkspaceSelectorProps> = ({ darkMode = false, onWorkspaceChange }) => {
    const { user, subscriptionStatus } = useUserContext();
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

    // Backend entitlement rules:
    // - premium: active | trialing
    // - free: none (or missing)
    const normalizedStatus = (subscriptionStatus || 'none').toLowerCase();
    const isFreeUser = normalizedStatus === 'none';

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
                    if (onWorkspaceChange) {
                        onWorkspaceChange(selectedWorkspace.id);
                    }
                } else {
                    setCurrentWorkspace(null);
                }
            } else {
                setCurrentWorkspace(null);
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
        if (isFreeUser && workspaces.length >= 1) {
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

            addToast('Workspace renamed successfully!', 'success');
            setShowRenameModal(false);
            setRenameWorkspaceName('');
            setWorkspaceToEdit(null);
            await fetchWorkspaces();
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
                    <button 
                        className="workspace-info"
                        onClick={() => setShowDropdown(!showDropdown)}
                    >
                        <span className="workspace-icon">📁</span>
                        <div className="workspace-details">
                            <span className="workspace-label">Workspace</span>
                            <span className="workspace-name">
                                {currentWorkspace?.name || 'My Workspace'}
                            </span>
                        </div>
                        <span className="dropdown-icon">{showDropdown ? '▲' : '▼'}</span>
                    </button>
                    
                    <button
                        className="create-workspace-btn"
                        onClick={() => setShowCreateModal(true)}
                        title={isFreeUser && workspaces.length >= 1 ? 'Upgrade to create more workspaces' : 'Create new workspace'}
                    >
                        <span className="btn-icon">+</span>
                        <span className="btn-text">New</span>
                    </button>
                </div>

                {/* Workspace Dropdown */}
                {showDropdown && workspaces.length > 1 && (
                    <div className="workspace-dropdown">
                        {workspaces.map((workspace) => (
                            <div key={workspace.id} className="workspace-item-wrapper">
                                <button
                                    className={`workspace-item ${currentWorkspace?.id === workspace.id ? 'active' : ''}`}
                                    onClick={() => handleWorkspaceSelect(workspace)}
                                >
                                    <span className="workspace-item-icon">📁</span>
                                    <span className="workspace-item-name">{workspace.name}</span>
                                    {currentWorkspace?.id === workspace.id && (
                                        <span className="check-icon">✓</span>
                                    )}
                                </button>
                                <div className="workspace-item-actions">
                                    <button
                                        className="workspace-action-btn edit-btn"
                                        onClick={(e) => handleRenameClick(workspace, e)}
                                        title="Rename workspace"
                                    >
                                        ✏️
                                    </button>
                                    {workspaces.length > 1 && (
                                        <button
                                            className="workspace-action-btn delete-btn"
                                            onClick={(e) => handleDeleteClick(workspace, e)}
                                            title="Delete workspace"
                                        >
                                            🗑️
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                {isFreeUser && workspaces.length >= 1 && (
                    <div className="workspace-limit-banner">
                        <span>📦</span>
                        <span>Free plan: 1 workspace. <a href="/settings">Upgrade</a> for more!</span>
                    </div>
                )}
            </div>

            {/* Create Workspace Modal */}
            {showCreateModal && (
                <div className="modal-overlay" onClick={handleCancelCreate}>
                    <div 
                        className={`workspace-modal ${darkMode ? 'dark-mode' : ''}`}
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="modal-header">
                            <h3>Create New Workspace</h3>
                            <button 
                                className="close-btn"
                                onClick={handleCancelCreate}
                                aria-label="Close"
                            >
                                ×
                            </button>
                        </div>

                        <div className="modal-body">
                            <div className="form-group">
                                <label htmlFor="workspace-name">Workspace Name</label>
                                <input
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

                            {error && (
                                <div className="error-message">
                                    {error}
                                </div>
                            )}

                            {isFreeUser && workspaces.length >= 1 && (
                                <div className="upgrade-prompt">
                                    <p><strong>Free Tier Limit Reached</strong></p>
                                    <p>Upgrade to premium to create unlimited workspaces and unlock more features!</p>
                                    <a href="/settings" className="upgrade-link">View Plans →</a>
                                </div>
                            )}
                        </div>

                        <div className="modal-footer">
                            <button
                                className="cancel-btn"
                                onClick={handleCancelCreate}
                                disabled={isCreating}
                            >
                                Cancel
                            </button>
                            <button
                                className="create-btn"
                                onClick={handleCreateWorkspace}
                                disabled={isCreating || !newWorkspaceName.trim() || (isFreeUser && workspaces.length >= 1)}
                            >
                                {isCreating ? 'Creating...' : 'Create Workspace'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Rename Workspace Modal */}
            {showRenameModal && workspaceToEdit && (
                <div className="modal-overlay" onClick={handleCancelRename}>
                    <div 
                        className={`workspace-modal ${darkMode ? 'dark-mode' : ''}`}
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="modal-header">
                            <h3>Rename Workspace</h3>
                            <button 
                                className="close-btn"
                                onClick={handleCancelRename}
                                aria-label="Close"
                            >
                                ×
                            </button>
                        </div>

                        <div className="modal-body">
                            <div className="form-group">
                                <label htmlFor="rename-workspace-name">New Name</label>
                                <input
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

                            {error && (
                                <div className="error-message">
                                    {error}
                                </div>
                            )}
                        </div>

                        <div className="modal-footer">
                            <button
                                className="cancel-btn"
                                onClick={handleCancelRename}
                                disabled={isRenaming}
                            >
                                Cancel
                            </button>
                            <button
                                className="create-btn"
                                onClick={handleRenameWorkspace}
                                disabled={isRenaming || !renameWorkspaceName.trim()}
                            >
                                {isRenaming ? 'Renaming...' : 'Rename'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Delete Workspace Modal */}
            {showDeleteModal && workspaceToEdit && (
                <div className="modal-overlay" onClick={handleCancelDelete}>
                    <div 
                        className={`workspace-modal delete-modal ${darkMode ? 'dark-mode' : ''}`}
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="modal-header">
                            <h3>Delete Workspace</h3>
                            <button 
                                className="close-btn"
                                onClick={handleCancelDelete}
                                aria-label="Close"
                            >
                                ×
                            </button>
                        </div>

                        <div className="modal-body">
                            <div className="warning-message">
                                <span className="warning-icon">⚠️</span>
                                <div>
                                    <p><strong>Are you sure you want to delete "{workspaceToEdit.name}"?</strong></p>
                                    <p>This will permanently delete:</p>
                                    <ul>
                                        <li>All files in this workspace</li>
                                        <li>All associated key concepts, flashcards, and quizzes</li>
                                        <li>All chat history related to these files</li>
                                    </ul>
                                    <p><strong>This action cannot be undone.</strong></p>
                                </div>
                            </div>

                            {error && (
                                <div className="error-message">
                                    {error}
                                </div>
                            )}
                        </div>

                        <div className="modal-footer">
                            <button
                                className="cancel-btn"
                                onClick={handleCancelDelete}
                                disabled={isDeleting}
                            >
                                Cancel
                            </button>
                            <button
                                className="delete-confirm-btn"
                                onClick={handleDeleteWorkspace}
                                disabled={isDeleting}
                            >
                                {isDeleting ? 'Deleting...' : 'Delete Workspace'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default WorkspaceSelector;
