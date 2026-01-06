import React, { useState, useEffect, useCallback, useContext, useRef } from 'react';
import './KnowledgeBase.css';
import { UploadedFile } from './types';
import { KnownWebSocketMessage, FileStatusUpdatePayload, FileStatusUpdateMessage } from '../types/websocketTypes';
import Modal from './Modal';
import WorkspaceSelector from './WorkspaceSelector';
import UsageQuota from './UsageQuota';
import { useToast } from '../contexts/ToastContext';

import { useUserContext } from '../UserContext';

// Helper function to identify YouTube URLs with more robust detection
const isYouTubeUrl = (url: string): boolean => {
    // Add more comprehensive detection - check for common YouTube URL patterns
    const youtubePatterns = [
        'youtube.com', 
        'youtu.be',
        'youtube',
        'yt.com',
        // Add full URL pattern matching
        /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\/(.+)$/i
    ];
    
    // Check if the URL matches any of the patterns
    for (const pattern of youtubePatterns) {
        if (typeof pattern === 'string' && url.includes(pattern)) {
            console.log(`Detected YouTube URL: ${url} (matched pattern: ${pattern})`);
            return true;
        } else if (pattern instanceof RegExp && pattern.test(url)) {
            console.log(`Detected YouTube URL: ${url} (matched regex pattern)`);
            return true;
        }
    }
    
    return false;
};

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
    const [showMoveMenu, setShowMoveMenu] = useState<number | null>(null);
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
                setShowMoveMenu(null);
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

    const handlePageSizeChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
        const newSize = Number(e.target.value);
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

    return (
        <div className={`knowledgebase-container ${darkMode ? 'dark-mode' : ''}`}>
            <WorkspaceSelector darkMode={darkMode} onWorkspaceChange={handleWorkspaceChange} />
            <UsageQuota darkMode={darkMode} />
            <div className="knowledgebase-header">
                <h3>📚 Knowledge Base</h3>

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
                                            {isYouTubeUrl(currentFile.file_name) ? '🎬' :
                                             currentFile.file_name.endsWith('.mp4') ? '🎬' :
                                             currentFile.file_name.endsWith('.pdf') ? '📄' :
                                             currentFile.file_name.endsWith('.txt') ? '📝' :
                                             currentFile.file_name.endsWith('.md') ? '📝' :
                                             currentFile.file_name.endsWith('.jpg') || currentFile.file_name.endsWith('.jpeg') || currentFile.file_name.endsWith('.png') ? '🖼️' :
                                             '📄'}
                                        </span>
                                        
                                        <span className="file-link">
                                            <span className="file-name">
                                                {isYouTubeUrl(currentFile.file_name)
                                                    ? `YouTube: ${currentFile.file_name.length > 40 ? currentFile.file_name.substring(0, 37) + '...' : currentFile.file_name}`
                                                    : currentFile.file_name.length > 30
                                                        ? currentFile.file_name.substring(0, 27) + '...'
                                                        : currentFile.file_name}
                                            </span>
                                            <span className="file-status">
                                                {(() => {
                                                    switch (currentFile.status) {
                                                        case 'processed':
                                                            return '✓ Ready';
                                                        case 'failed':
                                                            return '❌ Failed';
                                                        case 'uploaded':
                                                            return '📤 Uploaded';
                                                        case 'extracting':
                                                            return '🔍 Extracting content...';
                                                        case 'embedding':
                                                            return '🧠 Generating embeddings...';
                                                        case 'storing':
                                                            return '💾 Storing content...';
                                                        case 'generating_concepts':
                                                            return '🧩 Generating key concepts...';
                                                        default:
                                                            return '⏳ Processing...';
                                                    }
                                                })()}
                                            </span>
                                        </span>
                                        
                                        <div className="file-actions">
                                        {workspaces.length > 1 && (
                                            <div className="move-menu-container">
                                                <button
                                                    className="kb-action-button move-button"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        setShowMoveMenu(showMoveMenu === currentFile.id ? null : currentFile.id);
                                                    }}
                                                    disabled={fileStatus[currentFile.id]?.isMoving || false}
                                                    title="Move to workspace"
                                                >
                                                    {fileStatus[currentFile.id]?.isMoving ? '⏳' : '📁'}
                                                </button>
                                                {showMoveMenu === currentFile.id && (
                                                    <div className="move-dropdown">
                                                        <div className="move-dropdown-header">Move to:</div>
                                                        {workspaces
                                                            .filter(ws => ws.id !== currentWorkspaceId)
                                                            .map(workspace => (
                                                                <button
                                                                    key={workspace.id}
                                                                    className="move-dropdown-item"
                                                                    onClick={(e) => {
                                                                        e.stopPropagation();
                                                                        handleMoveFile(currentFile.id, workspace.id);
                                                                    }}
                                                                >
                                                                    📁 {workspace.name}
                                                                </button>
                                                            ))}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                        <button
                                            className="kb-action-button"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                handleDeleteClick(currentFile, e);
                                            }}
                                            disabled={fileStatus[currentFile.id]?.isDeleting || false}
                                        >
                                            {fileStatus[currentFile.id]?.isDeleting ? 'Deleting...' : <span className="delete-icon">🗑️</span>}
                                        </button>
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
                <p>Use the + button in the chat to upload files or YouTube videos</p>
                <p>Processing happens automatically in the background</p>
                <p>Files are ready when marked with a ✅</p>
            </div>

            <div className="kb-pagination-controls">
                <button
                    onClick={() => handlePageChange(filePagination.page - 1)}
                    disabled={filePagination.page <= 1}
                    className="kb-action-button"
                >
                    &lt; Prev
                </button>
                <span>
                    Page {filePagination.page} of {Math.ceil(filePagination.totalItems / filePagination.pageSize) || 1}
                </span>
                <button
                    onClick={() => handlePageChange(filePagination.page + 1)}
                    disabled={filePagination.page * filePagination.pageSize >= filePagination.totalItems}
                    className="kb-action-button"
                >
                    Next &gt;
                </button>
                <select value={filePagination.pageSize} onChange={handlePageSizeChange} className="kb-page-size-selector">
                    <option value={10}>10 / page</option>
                    <option value={25}>25 / page</option>
                    <option value={50}>50 / page</option>
                </select>
            </div>


        </div>
    );
};

export default React.memo(KnowledgeBaseComponent);
