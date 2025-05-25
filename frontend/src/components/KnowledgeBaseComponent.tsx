import React, { useState, useRef } from 'react';
import './KnowledgeBaseComponent.css';
import { UploadedFile } from './types';
import Modal from './Modal';

interface KnowledgeBaseComponentProps {
    files: UploadedFile[];
    token: string;
    fetchFiles: () => void;
    onDeleteFile: (fileId: number) => void;
    onFileClick: (file: UploadedFile) => void;
    darkMode?: boolean;
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
    const handleFileClick = (file: UploadedFile) => {
        onFileClick(file);
    };

    const handleDeleteClick = (file: UploadedFile) => {
        const isConfirmed = window.confirm(`Are you sure you want to delete ${file.name}?`);
        if (isConfirmed) {
            onDeleteFile(file.id);
        }
    };

    return (
        <div className={`knowledgebase-container ${darkMode ? 'dark-mode' : ''}`}>
            <h3>📚 Knowledge Base</h3>
            <div className="file-status-legend">
                <p><span className="red-indicator"></span> Uploaded (Processing)</p>
                <p><span className="green-indicator"></span> Ready for Queries</p>
            </div>
            <ul className="file-list">
                {files.map((file) => (
                    <li key={file.id} className={file.processed ? 'processed-file' : 'not-processed-file'}>
                        <span
                            className={`file-link ${file.processed ? 'link-processed' : 'link-not-processed'}`}
                            onClick={() => handleFileClick(file)}
                        >
                            {file.name} {file.processed ? "✅ (Ready)" : "🕒 (Processing)"}
                        </span>
                        <button 
                            onClick={() => onDeleteFile(file.id)}
                            className="kb-action-button delete-button"
                        >
                            Delete
                        </button>
                    </li>
                ))}
            </ul>

            <div style={{ padding: '10px', borderTop: '1px solid #ccc', display: 'flex', alignItems: 'center', gap: '10px' }}>
                {/* Removed file input element */}
            </div>

        </div>
    );
};

export default KnowledgeBaseComponent;
