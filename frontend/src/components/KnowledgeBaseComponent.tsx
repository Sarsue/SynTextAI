import React, { useState, useEffect, useCallback } from 'react';
import './KnowledgeBase.css';
import { UploadedFile } from './types';
import { KnownWebSocketMessage, FileStatusUpdatePayload, FileStatusUpdateMessage } from '../types/websocketTypes';
import WorkspaceSelector from './WorkspaceSelector';
import UsageQuota from './UsageQuota';
import { useToast } from '../contexts/ToastContext';
import {
    Library,
    FileText,
    CheckCircle2,
    XCircle,
    Upload,
    Loader2,
    FolderInput,
    Trash2,
    ChevronLeft,
    ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuTrigger,
    DropdownMenuContent,
    DropdownMenuLabel,
    DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import {
    Select,
    SelectTrigger,
    SelectValue,
    SelectContent,
    SelectItem,
} from '@/components/ui/select';

import { useUserContext } from '../UserContext';


interface KnowledgeBaseComponentProps {
    onFileClick: (file: UploadedFile) => void;
    darkMode?: boolean;
    onWorkspaceChange?: (workspaceId: number | null) => void;
}

interface FileStatus {
    [key: number]: {
        isDeleting: boolean;
        isRetrying: boolean;
        errorMessage?: string;
    };
}

const KnowledgeBaseComponent: React.FC<KnowledgeBaseComponentProps> = ({ onFileClick, darkMode = false, onWorkspaceChange }) => {
    const {
        files, // Renamed from userFiles
        loadUserFiles,
        deleteFileFromContext,
        filePagination,
        isLoadingFiles,
        fileError: contextFileError,
    } = useUserContext();
interface FileStatusEntry { isDeleting?: boolean; isMoving?: boolean; }
    const [fileStatus, setFileStatus] = useState<{[key: number]: FileStatusEntry}>({});
    const [error, setError] = useState<string | null>(null);
    const [currentWorkspaceId, setCurrentWorkspaceId] = useState<number | null>(null);
    const [workspaces, setWorkspaces] = useState<Array<{id: number; name: string}>>([]);
    const { addToast } = useToast();
    const { user } = useUserContext(); 

    useEffect(() => {
        if (contextFileError) {
            setError(contextFileError);
        } else {
            setError(null); // Clear local error if context error is gone
        }
    }, [contextFileError]);

    // Initial load and load on page/pageSize/workspace change
    useEffect(() => {
        // Only filter by workspace if user has multiple workspaces AND currentWorkspaceId is set
        // If multiple workspaces but no currentWorkspaceId yet, don't load files (wait for workspace selection)
        if (workspaces.length > 1 && currentWorkspaceId === null) {
            // Don't load files yet, waiting for workspace to be selected
            return;
        }
        
        const filterWorkspaceId = workspaces.length > 1 ? currentWorkspaceId : null;
        loadUserFiles(filePagination.page, filePagination.pageSize, filterWorkspaceId);
    }, [loadUserFiles, filePagination.page, filePagination.pageSize, currentWorkspaceId, workspaces.length]);

    // Fetch workspaces for move menu
    useEffect(() => {
        if (user) {
            fetchWorkspaces();
        }
    }, [user]);

    const fetchWorkspaces = async () => {
        if (!user) return;
        try {
            const idToken = await user.getIdToken();
            const response = await fetch('/api/v1/workspaces', {
                headers: { 'Authorization': `Bearer ${idToken}` },
            });
            if (response.ok) {
                const data = await response.json();
                console.log('Fetched workspaces for move menu:', data.items);
                setWorkspaces(data.items || []);
            }
        } catch (err) {
            console.error('Error fetching workspaces:', err);
        }
    };

    // Handle workspace change
    const handleWorkspaceChange = (workspaceId: number) => {
        console.log('Workspace changed to:', workspaceId);
        setCurrentWorkspaceId(workspaceId);
        if (onWorkspaceChange) {
            onWorkspaceChange(workspaceId);
        }
        // Files will reload automatically via useEffect
    };

    // Move file to different workspace
    const handleMoveFile = async (fileId: number, targetWorkspaceId: number) => {
        if (!user) return;
        
        setFileStatus(prev => ({
            ...prev,
            [fileId]: { ...prev[fileId], isMoving: true }
        }));
        
        try {
            const idToken = await user.getIdToken();
            const response = await fetch(`/api/v1/files/${fileId}/workspace`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
                body: JSON.stringify({ workspace_id: targetWorkspaceId }),
            });

            if (response.ok) {
                addToast('File moved successfully', 'success');
                // Reload files to reflect the change
                await loadUserFiles(filePagination.page, filePagination.pageSize, currentWorkspaceId);
            } else {
                const data = await response.json();
                addToast(data.detail || 'Failed to move file', 'error');
            }
        } catch (error) {
            console.error('Error moving file:', error);
            addToast('Failed to move file', 'error');
        } finally {
            setFileStatus(prev => ({
                ...prev,
                [fileId]: { ...prev[fileId], isMoving: false }
            }));
        }
    };



    const handlePageChange = useCallback((newPage: number) => {
        loadUserFiles(newPage, filePagination.pageSize, currentWorkspaceId);
    }, [loadUserFiles, filePagination.pageSize, currentWorkspaceId]);

    const handlePageSizeChange = useCallback((newSize: number) => {
        loadUserFiles(1, newSize, currentWorkspaceId);
    }, [loadUserFiles, currentWorkspaceId]);

    const handleFileClick = (file: UploadedFile) => {
        // Propagate the click to the parent to open the file viewer.
        onFileClick(file);
    };

    const handleDeleteClick = (file: UploadedFile, e: React.MouseEvent) => {
        e.stopPropagation();
        const isConfirmed = window.confirm(`Are you sure you want to delete ${file.file_name}?`);
        if (isConfirmed) {
            setFileStatus(prev => ({
                ...prev,
                [file.id]: { isDeleting: true }
            }));
            
            deleteFileFromContext(file.id);
        }
    };

    const handleNextPage = () => {
        if (filePagination.page * filePagination.pageSize < filePagination.totalItems) {
            loadUserFiles(filePagination.page + 1, filePagination.pageSize, currentWorkspaceId);
        }
    };

    const getStatusDisplay = (status: UploadedFile['status']) => {
        switch (status) {
            case 'processed':
                return { icon: <CheckCircle2 className="size-3.5 text-green-600" />, label: 'Ready' };
            case 'failed':
                return { icon: <XCircle className="size-3.5 text-destructive" />, label: 'Failed' };
            case 'uploaded':
                return { icon: <Upload className="size-3.5" />, label: 'Uploaded' };
            case 'extracting':
                return { icon: <Loader2 className="size-3.5 animate-spin" />, label: 'Extracting content...' };
            case 'embedding':
                return { icon: <Loader2 className="size-3.5 animate-spin" />, label: 'Generating embeddings...' };
            case 'storing':
                return { icon: <Loader2 className="size-3.5 animate-spin" />, label: 'Storing content...' };
            case 'generating_concepts':
                return { icon: <Loader2 className="size-3.5 animate-spin" />, label: 'Generating key concepts...' };
            default:
                return { icon: <Loader2 className="size-3.5 animate-spin" />, label: 'Processing...' };
        }
    };

    return (
        <div className={`knowledgebase-container ${darkMode ? 'dark-mode' : ''}`}>
            <WorkspaceSelector darkMode={darkMode} onWorkspaceChange={handleWorkspaceChange} />
            <UsageQuota darkMode={darkMode} />
            <div className="knowledgebase-header">
                <h3><Library className="size-4" /> Knowledge Base</h3>
            </div>
            <div className="file-status-legend">
                <p><span className="status-indicator processing"></span> Processing</p>
                <p><span className="status-indicator ready"></span> Ready</p>
                <p><span className="status-indicator failed"></span> Failed</p>
            </div>
            <div className="kb-header">
                <h4>Your Files</h4>
            </div>
            {error && (
                <div className="error-message">
                    Error loading files: {error}
                </div>
            )}
            <ul className="file-list">
                {isLoadingFiles && files.length === 0 && (
                    <li className="empty-file-list">
                        Loading files...
                    </li>
                )}
                {!isLoadingFiles && files.length === 0 && (
                    <li className="empty-file-list">
                        No files uploaded yet
                    </li>
                )}
                {files.length > 0 && (
                    files.map((currentFile: UploadedFile) => {

                        return (
                            <li key={currentFile.id} className={`file-item ${
                                currentFile.status === 'processed' ? 'processed-file' :
                                currentFile.status === 'failed' ? 'failed-file' :
                                currentFile.status === 'uploaded' ? 'uploaded-file' : 'processing-file'}`}>
                                <div className="file-item-header">
                                    <div className="file-info" onClick={() => handleFileClick(currentFile)}>
                                        <span className="file-icon">
                                            <FileText className="size-4" />
                                        </span>

                                        <span className="file-link">
                                            <span className="file-name">
                                                {currentFile.file_name.length > 30
                                                    ? currentFile.file_name.substring(0, 27) + '...'
                                                    : currentFile.file_name}
                                            </span>
                                            <span className="file-status">
                                                {(() => {
                                                    const { icon, label } = getStatusDisplay(currentFile.status);
                                                    return <>{icon} {label}</>;
                                                })()}
                                            </span>
                                        </span>

                                        <div className="file-actions">
                                        {workspaces.length > 1 && (
                                            <DropdownMenu>
                                                <DropdownMenuTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon-sm"
                                                        onClick={(e) => e.stopPropagation()}
                                                        disabled={fileStatus[currentFile.id]?.isMoving || false}
                                                        title="Move to workspace"
                                                    >
                                                        {fileStatus[currentFile.id]?.isMoving
                                                            ? <Loader2 className="size-3.5 animate-spin" />
                                                            : <FolderInput className="size-3.5" />}
                                                    </Button>
                                                </DropdownMenuTrigger>
                                                <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                                                    <DropdownMenuLabel>Move to</DropdownMenuLabel>
                                                    {workspaces
                                                        .filter(ws => ws.id !== currentWorkspaceId)
                                                        .map(workspace => (
                                                            <DropdownMenuItem
                                                                key={workspace.id}
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    handleMoveFile(currentFile.id, workspace.id);
                                                                }}
                                                            >
                                                                <FolderInput className="size-3.5" />
                                                                {workspace.name}
                                                            </DropdownMenuItem>
                                                        ))}
                                                </DropdownMenuContent>
                                            </DropdownMenu>
                                        )}
                                        <Button
                                            variant="ghost"
                                            size="icon-sm"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                handleDeleteClick(currentFile, e);
                                            }}
                                            disabled={fileStatus[currentFile.id]?.isDeleting || false}
                                            title="Delete file"
                                        >
                                            {fileStatus[currentFile.id]?.isDeleting
                                                ? <Loader2 className="size-3.5 animate-spin" />
                                                : <Trash2 className="size-3.5" />}
                                        </Button>
                                        {/* Manual retry button removed, retry is automatic on click/expand for failed files */}
                                    </div>
                                    </div>
                                </div>
                            </li>
                        );
                    })
                )}
            </ul>



            <div className="kb-help-text">
                <p>Upload PDF, DOCX, or TXT files using the 📎 button in the chat.</p>
                <p>Processing happens automatically in the background.</p>
                <p>Files are ready when marked with a ✓.</p>
            </div>

            <div className="kb-pagination-controls">
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handlePageChange(filePagination.page - 1)}
                    disabled={filePagination.page <= 1}
                >
                    <ChevronLeft className="size-3.5" /> Prev
                </Button>
                <span className="kb-pagination-label">
                    Page {filePagination.page} of {Math.ceil(filePagination.totalItems / filePagination.pageSize) || 1}
                </span>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handlePageChange(filePagination.page + 1)}
                    disabled={filePagination.page * filePagination.pageSize >= filePagination.totalItems}
                >
                    Next <ChevronRight className="size-3.5" />
                </Button>
                <Select
                    value={String(filePagination.pageSize)}
                    onValueChange={(value) => handlePageSizeChange(Number(value))}
                >
                    <SelectTrigger size="sm" className="w-28">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="10">10 / page</SelectItem>
                        <SelectItem value="25">25 / page</SelectItem>
                        <SelectItem value="50">50 / page</SelectItem>
                    </SelectContent>
                </Select>
            </div>


        </div>
    );
};

export default React.memo(KnowledgeBaseComponent);
