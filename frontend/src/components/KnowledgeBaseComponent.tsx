import React, { useState, useEffect, useCallback, useContext, useRef } from 'react';
import './KnowledgeBase.css';
import { UploadedFile } from './types';
import { KnownWebSocketMessage, FileStatusUpdatePayload, FileStatusUpdateMessage } from '../types/websocketTypes';
import Modal from './Modal';

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
}

interface FileStatus {
    [key: number]: {
        isDeleting: boolean;
        isRetrying: boolean;
        errorMessage?: string;
    };
}

const KnowledgeBaseComponent: React.FC<KnowledgeBaseComponentProps> = ({ onFileClick, darkMode = false }) => {
    const {
        files, // Renamed from userFiles
        loadUserFiles,
        deleteFileFromContext,
        filePagination,
        isLoadingFiles,
        fileError: contextFileError,
    } = useUserContext();
interface FileStatusEntry { isDeleting?: boolean; }
    const [fileStatus, setFileStatus] = useState<{[key: number]: FileStatusEntry}>({});
    const [error, setError] = useState<string | null>(null); 

    useEffect(() => {
        if (contextFileError) {
            setError(contextFileError);
        } else {
            setError(null); // Clear local error if context error is gone
        }
    }, [contextFileError]);

    // Initial load and load on page/pageSize change from context
    useEffect(() => {
        loadUserFiles(filePagination.page, filePagination.pageSize);
    }, [loadUserFiles, filePagination.page, filePagination.pageSize]);



    const handlePageChange = useCallback((newPage: number) => {
        loadUserFiles(newPage, filePagination.pageSize);
    }, [loadUserFiles, filePagination.pageSize]);

    const handlePageSizeChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
        const newSize = Number(e.target.value);
        loadUserFiles(1, newSize);
    }, [loadUserFiles]);

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
            loadUserFiles(filePagination.page + 1, filePagination.pageSize);
        }
    };

    return (
        <div className={`knowledgebase-container ${darkMode ? 'dark-mode' : ''}`}>
            <div className="knowledgebase-header">
                <h3>📚 Knowledge Base</h3>
                <div className="pagination-controls">
                    <button 
                        onClick={() => handlePageChange(filePagination.page - 1)}
                        disabled={filePagination.page <= 1 || isLoadingFiles}
                        className="pagination-button"
                    >
                        Previous
                    </button>
                    <span>Page {filePagination.page}</span>
                    <button 
                        onClick={handleNextPage}
                        disabled={filePagination.page * filePagination.pageSize >= filePagination.totalItems || isLoadingFiles}
                        className="pagination-button"
                    >
                        Next
                    </button>
                    <select 
                        value={filePagination.pageSize} 
                        onChange={handlePageSizeChange}
                        className="page-size-selector"
                        disabled={isLoadingFiles}
                    >
                        <option value={10}>10 per page</option>
                        <option value={25}>25 per page</option>
                    </select>
                    {isLoadingFiles && <span className="loading-indicator">Loading...</span>}
                </div>
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
                                                {currentFile.status === 'processed' ? '✓ Ready' :
                                                 currentFile.status === 'failed' ? '❌ Failed' :
                                                 currentFile.status === 'processing' ? '⏳ Processing...' :
                                                 currentFile.status === 'extracted' ? '🔍 Extracting content...' :
                                                 currentFile.status === 'uploaded' ? '📤 Uploaded' :
                                                 '⏳ Processing...'}
                                            </span>
                                        </span>
                                        
                                        <div className="file-actions">
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

            <div className="kb-help-text">
                <p>Use the + button in the chat to upload files or YouTube videos</p>
                <p>Processing happens automatically in the background</p>
                <p>Files are ready when marked with a ✅</p>
            </div>

        </div>
    );
};

export default React.memo(KnowledgeBaseComponent);
