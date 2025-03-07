import React, { useState } from 'react';
import { useUserContext } from '../UserContext';
import './InputArea.css';

interface InputAreaProps {
    onSend: (message: string, files: File[]) => Promise<void>;
    isSending: boolean;
}

const InputArea: React.FC<InputAreaProps> = ({ onSend, isSending }) => {
    const [message, setMessage] = useState('');
    const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
    const { darkMode } = useUserContext();

    const handleSendClick = () => {
        if (!message.trim() && attachedFiles.length === 0) return;

        // Filter out unsupported files before sending to the backend
        const validFiles = attachedFiles.filter(isFileSupported);
        if (validFiles.length !== attachedFiles.length) {
            alert('Only PDF, video, image (JPG, PNG, GIF), and text files are supported.');
        } else if (validFiles.length > 10) {
            alert('There is a maximum limit of 10 files per Upload');
        } else {
            onSend(message, validFiles).then(() => {
                setMessage('');
                setAttachedFiles([]);
            });
        }
    };

    const isFileSupported = (file: File): boolean => {
        const supportedTypes = [
            // Document formats
            'application/pdf',         // PDF files
            'text/plain',              // Plain text files

            // Image formats
            'image/jpeg',              // JPEG image
            'image/png',               // PNG image
            'image/gif',               // GIF image

            // Video formats
            'video/mp4',               // MP4 video
            /*    'video/webm',              // WebM video
               'video/ogg',               // Ogg video
               'video/quicktime',         // MOV video (QuickTime)
               'video/x-msvideo',         // AVI video
               'video/x-matroska',        // MKV video
               'video/mpeg',    */           // MPEG video    // Plain text files
        ];
        return supportedTypes.includes(file.type);
    };

    const handleAttachment = (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || []);
        const validFiles = files.filter(isFileSupported); // Filter valid files

        if (validFiles.length === 0) {
            alert('Only PDF, video, image (JPG, PNG, GIF), and text files are supported.');
        } else if (validFiles.length > 10) {
            alert('There is a maximum limit of 10 files per Upload');
        } else {
            setAttachedFiles((prevFiles) => [...prevFiles, ...validFiles]);
        }

        e.target.value = ''; // Reset input value
    };

    const handleRemoveFile = (file: File) => {
        setAttachedFiles((prevFiles) => prevFiles.filter((f) => f !== file));
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
                placeholder="Type your message or paste a link..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isSending}
                aria-label="Message input area"
            />
            <div className="file-input">
                <label htmlFor="file-upload" className="custom-file-upload" aria-label="Attach a file">
                    📎
                </label>
                <input
                    id="file-upload"
                    type="file"
                    multiple
                    onChange={handleAttachment}
                    disabled={isSending}
                    aria-label="File upload input"
                />
            </div>
            <div className="attached-files">
                {attachedFiles.map((file, index) => (
                    <div key={index} className="attached-file">
                        <span>{file.name}</span>
                        <button onClick={() => handleRemoveFile(file)} aria-label="Remove attached file">X</button>
                    </div>
                ))}
            </div>
            <button
                onClick={handleSendClick}
                disabled={isSending}
                className="send-button"
                aria-label="Send message"
            >
                {isSending ? '💬' : '✉️'}
            </button>
        </div>
    );
};

export default InputArea;
