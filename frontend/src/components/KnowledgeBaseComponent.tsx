import React, { useState } from 'react';
import './KnowledgeBaseComponent.css';
import { UploadedFile } from './types';
import Modal from './Modal';

interface KnowledgeBaseComponentProps {
    files: UploadedFile[];
    onDeleteFile: (fileId: number) => void;
    onFileClick: (file: UploadedFile) => void;
    darkMode: boolean;
}

const KnowledgeBaseComponent: React.FC<KnowledgeBaseComponentProps> = ({ files, onDeleteFile, onFileClick, darkMode }) => {
    const [isSummaryModalOpen, setIsSummaryModalOpen] = useState(false);
    const [currentSummary, setCurrentSummary] = useState<{ title: string; content: string } | null>(null);

    const handleFileClick = (file: UploadedFile) => {
        onFileClick(file);
    };

    const handleDeleteClick = (file: UploadedFile) => {
        const isConfirmed = window.confirm(`Are you sure you want to delete ${file.name}?`);
        if (isConfirmed) {
            onDeleteFile(file.id);
        }
    };

    const handleShowSummary = (file: UploadedFile) => {
        setCurrentSummary({
            title: `Summary of ${file.name}`,
            content: `This is a static summary for the file "${file.name}".\n\nIn a real implementation, this would be fetched from the backend LLM.`
        });
        setIsSummaryModalOpen(true);
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
                        {file.processed && (
                             <button
                                className="summary-button"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    handleShowSummary(file);
                                }}
                                title="Show Summary"
                                aria-label={`Show summary for ${file.name}`}
                            >
                                📄
                            </button>
                        )}
                          <button
                            className="delete-button"
                            onClick={(e) => {
                                e.stopPropagation();
                                handleDeleteClick(file);
                            }}
                        >
                            X
                        </button>
                    </li>
                ))}
            </ul>
            {currentSummary && (
                <Modal
                    isOpen={isSummaryModalOpen}
                    onClose={() => setIsSummaryModalOpen(false)}
                    title={currentSummary.title}
                    darkMode={darkMode}
                >
                    <p>{currentSummary.content}</p>
                </Modal>
            )}
        </div>
    );
};

export default KnowledgeBaseComponent;
