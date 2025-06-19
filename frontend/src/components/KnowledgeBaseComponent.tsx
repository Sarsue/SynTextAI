import React, { useState, useEffect, useCallback, useContext, useRef } from 'react';
import './KnowledgeBase.css';
import { UploadedFile } from './types';
import { KnownWebSocketMessage, FileStatusUpdatePayload, FileStatusUpdateMessage } from '../types/websocketTypes';
import Modal from './Modal';
import { toast } from 'react-toastify';
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
    onDeleteFile: (fileId: number) => void;
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

const KnowledgeBaseComponent: React.FC<KnowledgeBaseComponentProps> = ({
    onDeleteFile,
    onFileClick,
    darkMode = false,
}) => {
    const {
        files, // Renamed from userFiles
        loadUserFiles,
        filePagination,
        isLoadingFiles,
        fileError: contextFileError,
    } = useUserContext();
    const [expandedFile, setExpandedFile] = useState<number | null>(null);
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

    const handleFileClick = async (file: UploadedFile) => {
        console.log(`Clicked file: ${file.name}, ID: ${file.id}, status: ${file.processing_status}, isYouTube: ${isYouTubeUrl(file.name)}`);
        
        // Refresh the file list to get the latest status
        try {
            onFileClick(file);
        } catch (error) {
            // If onFileClick itself throws, it will be caught here.
            // The primary purpose of onFileClick is to notify the parent.
            // If the parent's handler throws, it's an issue in the parent.
            console.error('Error during onFileClick callback execution in parent:', error);
        }
        
        // Expand the file details so delete button is visible
        setExpandedFile(file.id);
    };

    const handleDeleteClick = (file: UploadedFile, e: React.MouseEvent) => {
        e.stopPropagation();
        const isConfirmed = window.confirm(`Are you sure you want to delete ${file.name}?`);
        if (isConfirmed) {
            setFileStatus(prev => ({
                ...prev,
                [file.id]: { isDeleting: true }
            }));
            
            onDeleteFile(file.id);
        }
    };
    
    const toggleFileDetails = (fileId: number) => {
        // If the file is already expanded, close it; otherwise expand it
        const newExpandedFileId = expandedFile === fileId ? null : fileId;
        setExpandedFile(newExpandedFileId);
        
        // Log for debugging
        console.log(`Toggling file details for ID: ${fileId}, new state: ${newExpandedFileId ? 'expanded' : 'collapsed'}`);
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
                                currentFile.processing_status === 'processed' ? 'processed-file' :
                                currentFile.processing_status === 'failed' ? 'failed-file' :
                                currentFile.processing_status === 'uploaded' ? 'uploaded-file' : 'processing-file'}`}>
                                <div className="file-item-header">
                                    <div className="file-info" onClick={() => handleFileClick(currentFile)}>
                                        <span className="file-icon">
                                            {isYouTubeUrl(currentFile.name) ? '🎬' :
                                             currentFile.name.endsWith('.mp4') ? '🎬' :
                                             currentFile.name.endsWith('.pdf') ? '📄' :
                                             currentFile.name.endsWith('.txt') ? '📝' :
                                             currentFile.name.endsWith('.md') ? '📝' :
                                             currentFile.name.endsWith('.jpg') || currentFile.name.endsWith('.jpeg') || currentFile.name.endsWith('.png') ? '🖼️' :
                                             '📄'}
                                        </span>
                                        
                                        <span className="file-link">
                                            <span className="file-name">
                                                {isYouTubeUrl(currentFile.name)
                                                    ? `YouTube: ${currentFile.name.length > 40 ? currentFile.name.substring(0, 37) + '...' : currentFile.name}`
                                                    : currentFile.name.length > 30
                                                        ? currentFile.name.substring(0, 27) + '...'
                                                        : currentFile.name}
                                            </span>
                                            <span className="file-status">
                                                {currentFile.processing_status === 'processed' ? '✓ Ready' :
                                                 currentFile.processing_status === 'failed' ? '❌ Failed' :
                                                 currentFile.processing_status === 'processing' ? '⏳ Processing...' :
                                                 currentFile.processing_status === 'extracted' ? '🔍 Extracting content...' :
                                                 currentFile.processing_status === 'uploaded' ? '📤 Uploaded' :
                                                 '⏳ Processing...'}
                                                {currentFile.error_message && ` (${currentFile.error_message})`}
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

            <div className="kb-help-text">
                <p>Use the + button in the chat to upload files or YouTube videos</p>
                <p>Processing happens automatically in the background</p>
                <p>Files are ready when marked with a ✅</p>
            </div>

        </div>
    );
};

export default KnowledgeBaseComponent;
