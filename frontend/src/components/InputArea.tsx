import React, { useState } from 'react';
import { useUserContext } from '../UserContext';
import VoiceInput from './VoiceInput';
import './InputArea.css';

interface InputAreaProps {
    onSend: (message: string, files: File[]) => Promise<void>;
    isSending: boolean;
    token: string | null;
    onContentAdded: () => void;
}

const InputArea: React.FC<InputAreaProps> = ({ 
    onSend, 
    isSending, 
    token, 
    onContentAdded 
}) => {
    const [message, setMessage] = useState('');
    const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
    const { darkMode } = useUserContext();
    const [youtubeUrl, setYoutubeUrl] = useState('');
    const [showYoutubeInput, setShowYoutubeInput] = useState(false);

    const handleVoiceInput = (transcript: string) => {
        console.log('Received Voice Transcript:', transcript);
        setMessage(prevMessage => {
            const updatedMessage = prevMessage.trim() 
                ? `${prevMessage.trim()} ${transcript}` 
                : transcript;
            
            console.log('Updated Message:', updatedMessage);
            return updatedMessage;
        });
    };

    const handleSendClick = () => {
        if (!message.trim() && attachedFiles.length === 0) return;

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
            'application/pdf',         // PDF files
            'text/plain',              // Plain text files

            'image/jpeg',              // JPEG image
            'image/png',               // PNG image
            'image/gif',               // GIF image

            'video/mp4',               // MP4 video
        ];
        return supportedTypes.includes(file.type);
    };

    const handleAttachment = (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || []);
        const validFiles = files.filter(isFileSupported); 

        if (validFiles.length === 0) {
            alert('Only PDF, video, image (JPG, PNG, GIF), and text files are supported.');
        } else if (validFiles.length > 10) {
            alert('There is a maximum limit of 10 files per Upload');
        } else {
            setAttachedFiles((prevFiles) => [...prevFiles, ...validFiles]);
        }

        e.target.value = ''; 
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

    const handleYoutubeIconClick = () => {
        setShowYoutubeInput(!showYoutubeInput);
    };

    const handleAddYoutubeVideo = async () => {
        if (!youtubeUrl || !token) {
            console.warn('YouTube URL or Token missing');
            return;
        }
        try {
            // Corrected API endpoint and body structure to match backend
            const response = await fetch(`/api/v1/files`, { 
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json', // Explicitly set content type
                    'Authorization': `Bearer ${token}`
                },
                // Updated body structure
                body: JSON.stringify({ type: 'youtube', url: youtubeUrl }), 
            });
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to add YouTube video');
            }
            setYoutubeUrl('');         
            setShowYoutubeInput(false); 
            onContentAdded();         
        } catch (error) {
            console.error('Error adding YouTube video:', error);
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
            <div className="attached-files">
                {attachedFiles.map((file, index) => (
                    <div key={index} className="attached-file">
                        <span>{file.name}</span>
                        <button onClick={() => handleRemoveFile(file)} aria-label="Remove attached file">X</button>
                    </div>
                ))}
            </div>
            {showYoutubeInput && (
                <div className="youtube-input-container" style={{ padding: '5px 0' }}>
                    <input
                        type="text"
                        value={youtubeUrl}
                        onChange={(e) => setYoutubeUrl(e.target.value)}
                        placeholder="YouTube Video URL"
                        style={{ flexGrow: 1, padding: '8px', marginRight: '5px', borderRadius: '4px', border: darkMode ? '1px solid #555' : '1px solid #ccc' }}
                        disabled={isSending} 
                    />
                    <button 
                        onClick={handleAddYoutubeVideo} 
                        style={{ padding: '8px 15px', borderRadius: '4px' }} 
                        disabled={isSending || !youtubeUrl}
                    >
                        Add Video
                    </button>
                </div>
            )}
            <div className="input-controls">
                <div className="left-controls">
                <VoiceInput
                        onTranscript={handleVoiceInput}
                        darkMode={darkMode}
                    />
                    <label htmlFor="file-upload" className="control-button" aria-label="Attach a file">
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
                    <span 
                        className="control-button youtube-icon" 
                        onClick={handleYoutubeIconClick} 
                        aria-label="Add YouTube Video"
                        style={{ cursor: 'pointer', color: 'red' }} 
                    >
                        📺 
                    </span>
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
        </div>
    );
};

export default InputArea;
