import React, { useState } from 'react';
import { useDarkMode } from '../DarkModeContext';
import './InputArea.css';

interface InputAreaProps {
    onSend: (message: string, files: File[]) => Promise<void>;
    isSending: boolean;
}

const InputArea: React.FC<InputAreaProps> = ({ onSend, isSending }) => {
    const [message, setMessage] = useState('');
    const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
    const { darkMode, setDarkMode } = useDarkMode(); // Access the darkMode state and setDarkMode function


    const handleSendClick = () => {
        if (!message.trim() && attachedFiles.length === 0) {
            return;
        }

        onSend(message, attachedFiles);
        setMessage('');
        setAttachedFiles([]);
    };

    const handleAttachment = (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || []);
        setAttachedFiles([...attachedFiles, ...files]);
        e.target.value = '';
    };

    const handleRemoveFile = (file: File) => {
        const updatedFiles = attachedFiles.filter((f) => f !== file);
        setAttachedFiles(updatedFiles);
    };
    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendClick();
        }
    };

    return (
        <div className={`input-area ${darkMode ? 'dark-mode' : ''}`}>
            <textarea
                className="text-input"
                placeholder="Type your message here or paste link here..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isSending}
            />
            <div className="file-input">
                <label htmlFor="file-upload" className="custom-file-upload">📎 Attach Files</label>
                <input
                    id="file-upload"
                    type="file"
                    multiple
                    onChange={handleAttachment}
                    disabled={isSending}
                />
            </div>
            <div className="attached-files">
                {attachedFiles.map((file, index) => (
                    <div key={index} className="attached-file">
                        <span>{file.name}</span>
                        <button onClick={() => handleRemoveFile(file)}>X</button>
                    </div>
                ))}
            </div>
            <button onClick={handleSendClick} disabled={isSending} className="send-button">
                {isSending ? '⏳' : '✉️'}
            </button>
        </div>
    );
};

export default InputArea;