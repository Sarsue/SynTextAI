import React from 'react';
import './KnowledgeBaseComponent.css';
import { UploadedFile } from './types';


interface KnowledgeBaseComponentProps {
    files: UploadedFile[];
    onDeleteFile: (fileId: number) => void;
    onFileClick: (file: UploadedFile) => void; // Ensure onFileClick is passed and used
    darkMode: boolean;
}

const KnowledgeBaseComponent: React.FC<KnowledgeBaseComponentProps> = ({ files, onDeleteFile, onFileClick, darkMode }) => {

    const handleFileClick = (file: UploadedFile) => {
        onFileClick(file); // Call onFileClick when a file is clicked
    };

    const handleDeleteClick = (file: UploadedFile) => {
        const isConfirmed = window.confirm(`Are you sure you want to delete ${file.name}?`);
        if (isConfirmed) {
            onDeleteFile(file.id);
        }
    };

    return (
        <div className={`knowledgebase-container ${darkMode ? 'dark-mode' : ''}`}>
            <h3> 📝 💼</h3>
            <ul className="file-list">
                {files.map((file) => (
                    <li key={file.id} className={file.processed ? 'processed-file' : 'not-processed-file'}>
                        <span
                            className={`file-link ${file.processed ? 'link-processed' : 'link-not-processed'}`}
                            onClick={() => handleFileClick(file)}
                        >
                            {file.name}
                        </span>
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
        </div>
    );

};

export default KnowledgeBaseComponent;
