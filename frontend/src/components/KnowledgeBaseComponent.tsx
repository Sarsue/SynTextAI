import React, { useState, useEffect, useCallback } from 'react';
import './KnowledgeBase.css';
import { UploadedFile } from './types';
import Modal from './Modal';
import { toast } from 'react-toastify';

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
    token: string;
    fetchFiles: (page: number, pageSize: number) => Promise<{ items: UploadedFile[], total: number }>;
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
    token,
    fetchFiles,
    onDeleteFile,
    onFileClick,
    darkMode = false,
}) => {
    const [files, setFiles] = useState<UploadedFile[]>([]);
    const [expandedFile, setExpandedFile] = useState<number | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [pagination, setPagination] = useState({
        page: 1,
        pageSize: 10,
        totalItems: 0,
    });
    const [fileStatus, setFileStatus] = useState<FileStatus>({});

    const loadFiles = useCallback(async (page: number, pageSize: number) => {
        try {
            setIsLoading(true);
            const { items, total } = await fetchFiles(page, pageSize);
            setFiles(items);
            setPagination(prev => ({
                ...prev,
                totalItems: total
            }));
        } catch (error) {
            console.error('Error loading files:', error);
        } finally {
            setIsLoading(false);
        }
    }, [fetchFiles]);

    // Initial load
    useEffect(() => {
        loadFiles(pagination.page, pagination.pageSize);
    }, [loadFiles]);

    const handlePageChange = useCallback((newPage: number) => {
        setPagination(prev => ({
            ...prev,
            page: newPage
        }));
        loadFiles(newPage, pagination.pageSize);
    }, [pagination.pageSize, loadFiles]);

    const handlePageSizeChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
        const newSize = Number(e.target.value);
        setPagination(prev => ({
            ...prev,
            page: 1,
            pageSize: newSize
        }));
        loadFiles(1, newSize);
    }, [loadFiles]);

    const handleFileClick = (file: UploadedFile) => {
        console.log(`Clicked file: ${file.name}, ID: ${file.id}, status: ${file.processing_status}, isYouTube: ${isYouTubeUrl(file.name)}`);
        
        // Show the file content in the viewer
        onFileClick(file);
        
        // Expand the file details so delete button is visible
        setExpandedFile(file.id);
    };

    const handleDeleteClick = (file: UploadedFile, e: React.MouseEvent) => {
        e.stopPropagation();
        const isConfirmed = window.confirm(`Are you sure you want to delete ${file.name}?`);
        if (isConfirmed) {
            setFileStatus(prev => ({
                ...prev,
                [file.id]: { ...prev[file.id], isDeleting: true }
            }));
            
            onDeleteFile(file.id);
            
            // Refresh files after deletion
            loadFiles(pagination.page, pagination.pageSize);
        }
    };
    
    const toggleFileDetails = (fileId: number) => {
        // If the file is already expanded, close it; otherwise expand it
        const newExpandedFileId = expandedFile === fileId ? null : fileId;
        setExpandedFile(newExpandedFileId);
        
        // Log for debugging
        console.log(`Toggling file details for ID: ${fileId}, new state: ${newExpandedFileId ? 'expanded' : 'collapsed'}`);
    };

    return (
        <div className={`knowledgebase-container ${darkMode ? 'dark-mode' : ''}`}>
            <div className="knowledgebase-header">
                <h3>📚 Knowledge Base</h3>
                <div className="pagination-controls">
                    <button 
                        onClick={() => handlePageChange(pagination.page - 1)}
                        disabled={pagination.page === 1 || isLoading}
                        className="pagination-button"
                    >
                        Previous
                    </button>
                    <span>Page {pagination.page}</span>
                    <button 
                        onClick={() => handlePageChange(pagination.page + 1)}
                        disabled={isLoading || (files.length < pagination.pageSize && files.length < pagination.totalItems)}
                        className="pagination-button"
                    >
                        Next
                    </button>
                    <select 
                        value={pagination.pageSize} 
                        onChange={handlePageSizeChange}
                        className="page-size-selector"
                        disabled={isLoading}
                    >
                        <option value={10}>10 per page</option>
                        <option value={25}>25 per page</option>
                    </select>
                    {isLoading && <span className="loading-indicator">Loading...</span>}
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
            <ul className="file-list">
                {files.length === 0 ? (
                    <li className="empty-file-list">No files uploaded yet</li>
                ) : (
                    files.map((file) => {
                        const status = fileStatus[file.id] || { isDeleting: false, isRetrying: false };
                        
                        return (
                            <li key={file.id} className={`file-item ${
                                file.processing_status === 'processed' ? 'processed-file' : 
                                file.processing_status === 'failed' ? 'failed-file' : 
                                file.processing_status === 'uploaded' ? 'uploaded-file' : 'processing-file'}`}>
                                <div className="file-item-header">
                                    <div className="file-info" onClick={() => handleFileClick(file)}>
                                        <span className="file-icon">
                                            {isYouTubeUrl(file.name) ? '🎬' : 
                                             file.name.endsWith('.mp4') ? '🎬' : 
                                             file.name.endsWith('.pdf') ? '📄' : 
                                             file.name.endsWith('.txt') ? '📝' : 
                                             file.name.endsWith('.md') ? '📝' : 
                                             file.name.endsWith('.jpg') || file.name.endsWith('.jpeg') || file.name.endsWith('.png') ? '🖼️' : 
                                             '📄'}
                                        </span>
                                        
                                        <span className="file-link">
                                            <span className="file-name">
                                                {isYouTubeUrl(file.name) 
                                                    ? `YouTube: ${file.name.length > 40 ? file.name.substring(0, 37) + '...' : file.name}`
                                                    : file.name.length > 30 
                                                        ? file.name.substring(0, 27) + '...' 
                                                        : file.name}
                                            </span>
                                            <span className="file-status">
                                                {file.processing_status === 'processed' ? '✓ Ready' : 
                                                 file.processing_status === 'failed' ? '❌ Failed' : 
                                                 file.processing_status === 'processing' ? '⏳ Processing...' : 
                                                 file.processing_status === 'extracted' ? '🔍 Extracting content...' : 
                                                 file.processing_status === 'uploaded' ? '📤 Uploaded' : 
                                                 '⏳ Processing...'}
                                                {file.error_message && ` (${file.error_message})`}
                                            </span>
                                        </span>
                                        
                                        <div className="file-actions">
                                        <button 
                                            className="kb-action-button"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                handleDeleteClick(file, e);
                                            }}
                                            disabled={status.isDeleting}
                                        >
                                            {status.isDeleting ? 'Deleting...' : 'Delete'}
                                        </button>
                                        {file.processing_status === 'failed' && (
                                            <span className="file-error">
                                                {file.error_message || 'Processing failed'}
                                            </span>
                                        )}
                                    </div>
                                    </div>
                                    {/* Removed expand/collapse arrow since it's redundant with always-visible delete button */}
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
