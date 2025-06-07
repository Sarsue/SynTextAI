import React, { useState, useEffect } from 'react';
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
    files: UploadedFile[];
    token: string;
    fetchFiles: () => void;
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
    files, 
    token, 
    fetchFiles, 
    onDeleteFile, 
    onFileClick, 
    darkMode 
}) => {
    const [isSummaryModalOpen, setIsSummaryModalOpen] = useState(false);
    const [currentSummary, setCurrentSummary] = useState<{ title: string; content: string; fileName?: string } | null>(null);
    const [fileStatus, setFileStatus] = useState<FileStatus>({});
    const [expandedFile, setExpandedFile] = useState<number | null>(null);
    
    // Periodically refresh files that are in processing state
    useEffect(() => {
        const processingFiles = files.filter(file => !file.processed);
        if (processingFiles.length > 0) {
            const interval = setInterval(() => {
                fetchFiles();
            }, 15000); // Check every 15 seconds for better responsiveness
            
            return () => clearInterval(interval);
        }
    }, [files, fetchFiles]);
    const handleFileClick = (file: UploadedFile) => {
        console.log(`Clicked file: ${file.name}, ID: ${file.id}, isYouTube: ${isYouTubeUrl(file.name)}`);
        
        // Show the file content in the viewer
        onFileClick(file);
        
        // CRITICAL: Always expand the file details so delete button is visible
        // This ensures the delete button is accessible for all file types including YouTube
        setExpandedFile(file.id);
        
        // Temporarily disabled automatic reprocessing
        // if (!file.processed) {
        //     retryProcessingFile(file);
        // }
    };

    const handleDeleteClick = (file: UploadedFile) => {
        const isConfirmed = window.confirm(`Are you sure you want to delete ${file.name}?`);
        if (isConfirmed) {
            setFileStatus(prev => ({
                ...prev,
                [file.id]: { ...prev[file.id], isDeleting: true }
            }));
            
            onDeleteFile(file.id);
            
            // Reset status after a timeout in case the delete operation doesn't trigger a refresh
            setTimeout(() => {
                setFileStatus(prev => {
                    const newStatus = {...prev};
                    if (newStatus[file.id]) {
                        newStatus[file.id].isDeleting = false;
                    }
                    return newStatus;
                });
            }, 5000);
        }
    };
    
    // Private method for automatic retrying of file processing
    const retryProcessingFile = async (file: UploadedFile) => {
        try {
            // Call API to retry processing using the reextract endpoint without UI feedback
            const response = await fetch(`/api/v1/files/${file.id}/reextract`, {
                method: 'PATCH',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                console.warn(`Auto-reprocessing failed for file ${file.name}: ${errorData.detail}`);
                return;
            }
            
            console.log(`Auto-reprocessing started for ${file.name}`);
            // Refresh the file list to get updated status
            setTimeout(() => fetchFiles(), 2000);
            
        } catch (error) {
            console.warn('Error in auto-retry processing:', error);
        }
    };
    
    const toggleFileDetails = (fileId: number) => {
        // If the file is already expanded, close it; otherwise expand it
        const newExpandedFileId = expandedFile === fileId ? null : fileId;
        setExpandedFile(newExpandedFileId);
        
        // Log for debugging
        console.log(`Toggling file details for ID: ${fileId}, new state: ${newExpandedFileId ? 'expanded' : 'collapsed'}`);
        
        // Temporarily disabled automatic reprocessing
        // if (newExpandedFileId !== null) {
        //     const file = files.find((f: UploadedFile) => f.id === newExpandedFileId);
        //     console.log('Found file for expanding:', file);
        //     if (file && !file.processed) {
        //         retryProcessingFile(file);
        //     }
        // }
    };

    return (
        <div className={`knowledgebase-container ${darkMode ? 'dark-mode' : ''}`}>
            <h3>📚 Knowledge Base</h3>
            <div className="file-status-legend">
                <p><span className="red-indicator"></span> Processing</p>
                <p><span className="green-indicator"></span> Ready</p>
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
                            <li key={file.id} className={`file-item ${file.processed ? 'processed-file' : 'not-processed-file'}`}>
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
                                            
                                        <span 
                                            className={`file-link ${file.processed ? 'link-processed' : 'link-not-processed'}`}
                                        >
                                            <span className="file-name">
                                                {isYouTubeUrl(file.name) ? 
                                                    // For YouTube URLs, show a more readable name
                                                    `YouTube: ${file.name.length > 40 ? file.name.substring(0, 37) + '...' : file.name}`
                                                    : 
                                                    // For other files, show the full name
                                                    file.name
                                                }
                                            </span>
                                            <span className="file-status">
                                                {file.processed ? "✅" : "🕒"}
                                            </span>
                                        </span>
                                        
                                        <button 
                                            onClick={(e) => {
                                                e.stopPropagation(); // Prevent opening the file viewer
                                                handleDeleteClick(file);
                                            }}
                                            className="kb-action-button delete-button always-visible"
                                            disabled={fileStatus[file.id]?.isDeleting}
                                            title="Delete file"
                                        >
                                            {fileStatus[file.id]?.isDeleting ? "Deleting..." : "Delete"}
                                        </button>
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
