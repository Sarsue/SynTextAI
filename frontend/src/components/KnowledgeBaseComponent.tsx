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

    const handleShowSummary = (file: UploadedFile) => {
        if (file.summary) { 
            setCurrentSummary({
                title: `Summary of ${file.name}`,
                content: file.summary, 
                fileName: `${file.name}_summary.txt`
            });
            setIsSummaryModalOpen(true);
        } else {
            console.log(`Summary not available for ${file.name}. Modal not opened.`);
        }
    };

    const handleDownloadSummary = () => {
        if (!currentSummary || !currentSummary.content || !currentSummary.fileName) return;

        const blob = new Blob([currentSummary.content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = currentSummary.fileName;
        document.body.appendChild(link); 
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url); 
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
                            {/^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//.test(file.publicUrl || file.name) ? (
                                <span title="YouTube Video" style={{marginRight: 4}}>📺</span>
                            ) : null}
                            {file.name} {file.processed ? "✅ (Ready)" : "🕒 (Processing)"}
                        </span>
                        {file.processed && (
                            <button
                                className={`summary-button ${!file.summary ? 'disabled' : ''}`}
                                onClick={(e) => {
                                    e.stopPropagation();
                                    if (file.summary) handleShowSummary(file); 
                                }}
                                title={file.summary ? "Show Summary" : "Summary Not Available"}
                                aria-label={file.summary ? `Show summary for ${file.name}` : `Summary not available for ${file.name}`}
                                disabled={!file.summary}
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
                    onDownload={handleDownloadSummary}
                >
                    <p>{currentSummary.content}</p>
                </Modal>
            )}
        </div>
    );
};

export default KnowledgeBaseComponent;
