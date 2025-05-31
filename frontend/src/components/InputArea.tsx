import React, { useState } from 'react';
import { toast } from 'react-toastify';
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
    // Reference to the YouTube input element for focus management
    const youtubeInputRef = React.useRef<HTMLInputElement>(null);
    const [showYoutubeInput, setShowYoutubeInput] = useState(false);
    const [showAddMenu, setShowAddMenu] = useState(false);

    const handleVoiceInput = (transcript: string) => {
        setMessage(prevMessage => {
            const updatedMessage = prevMessage.trim()
                ? `${prevMessage.trim()} ${transcript}`
                : transcript;
            return updatedMessage;
        });
    };

    const isFileSupported = (file: File): boolean => {
        const supportedTypes = [
            'application/pdf', 'text/plain',
            'image/jpeg', 'image/png', 'image/gif',
            'video/mp4',
        ];
        return supportedTypes.includes(file.type);
    };

    const handleSendClick = () => {
        if (!message.trim() && attachedFiles.length === 0) {
            toast.info("Nothing to send. Please type a message or attach files.");
            return;
        }

        const filesToSend = attachedFiles.filter(isFileSupported); // Ensure only supported files are sent
        let canProceed = true;

        if (filesToSend.length !== attachedFiles.length && attachedFiles.length > 0) {
            toast.warn('Some attached files have unsupported types and will be ignored.');
        }

        if (filesToSend.length > 10) {
            toast.error('Cannot send: Maximum of 10 files allowed per upload.');
            canProceed = false;
        }

        if (canProceed && (message.trim() || filesToSend.length > 0)) {
            onSend(message, filesToSend).then(() => {
                setMessage('');
                setAttachedFiles([]);
            });
        } else if (canProceed && !message.trim() && filesToSend.length === 0 && attachedFiles.length > 0) {
            // This case means all attached files were unsupported, and there's no message
            toast.error('No valid files to send, and no message typed.');
        }
    };

    const handleAttachment = (e: React.ChangeEvent<HTMLInputElement>) => {
        const allSelectedFiles = Array.from(e.target.files || []);
        if (allSelectedFiles.length === 0) return;

        const newlySupportedFiles = allSelectedFiles.filter(isFileSupported);

        if (allSelectedFiles.length > 0 && newlySupportedFiles.length === 0) {
            toast.error('No supported files selected. Please choose PDF, video, image (JPG, PNG, GIF), or text files.');
        } else {
            if (allSelectedFiles.length > newlySupportedFiles.length && newlySupportedFiles.length > 0) {
                toast.info(`${allSelectedFiles.length - newlySupportedFiles.length} file(s) were not added due to unsupported type.`);
            }

            if (newlySupportedFiles.length > 0) {
                setAttachedFiles((currentAttachedFiles: File[]) => {
                    const combinedFiles = [...currentAttachedFiles, ...newlySupportedFiles];
                    if (combinedFiles.length > 10) {
                        const spaceRemaining = 10 - currentAttachedFiles.length;
                        if (spaceRemaining > 0) {
                            toast.warn(`Maximum of 10 files. Adding first ${spaceRemaining} of the selected supported files.`);
                            return [...currentAttachedFiles, ...newlySupportedFiles.slice(0, spaceRemaining)];
                        }
                        toast.error(`Cannot add more files. Maximum of 10 files already attached.`);
                        return currentAttachedFiles;
                    }
                    return combinedFiles;
                });
            }
        }
        e.target.value = ''; // Clear the input
    };

    const handleRemoveFile = (fileToRemove: File) => {
        setAttachedFiles((prevFiles: File[]) => prevFiles.filter((f: File) => f !== fileToRemove));
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
    
    const toggleAddMenu = () => {
        setShowAddMenu(!showAddMenu);
        // Close YouTube input if dropdown is closed and YouTube was showing
        if (showAddMenu && showYoutubeInput) {
            setShowYoutubeInput(false);
        }
    };
    
    // Focus YouTube input when it appears
    React.useEffect(() => {
        if (showYoutubeInput && youtubeInputRef.current) {
            // Small delay to ensure the input is rendered
            setTimeout(() => {
                youtubeInputRef.current?.focus();
            }, 50);
        } else if (!showYoutubeInput) {
            // Clear YouTube URL when input is closed
            setYoutubeUrl('');
        }
    }, [showYoutubeInput]);

    // Handle clicking outside to close the dropdown and YouTube input
    React.useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            const target = event.target as HTMLElement;
            
            // Close dropdown if open and clicked outside
            if (showAddMenu && !target.closest('.add-content-menu') && !target.closest('.add-content-button')) {
                setShowAddMenu(false);
            }
            
            // Close YouTube input if open and clicked outside
            if (showYoutubeInput && 
                !target.closest('.youtube-input-container') && 
                !target.closest('.add-content-menu')) {
                setShowYoutubeInput(false);
            }
        };
        
        document.addEventListener('mousedown', handleClickOutside);
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [showAddMenu, showYoutubeInput]);

    const handleAddYoutubeVideo = async () => {
        if (!youtubeUrl.trim()) {
            toast.error('Please enter a YouTube Video URL.');
            return;
        }
        if (!token) {
            toast.error('Authentication token not found. Please log in again.');
            return;
        }
        try {
            const response = await fetch(`/api/v1/files`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({ type: 'youtube', url: youtubeUrl }),
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: 'Failed to parse error response.' }));
                throw new Error(errorData.detail || 'Failed to add YouTube video');
            }
            setYoutubeUrl('');
            setShowYoutubeInput(false);
            onContentAdded();
            toast.success('YouTube video added and is now processing.');
        } catch (error) {
            console.error('Error adding YouTube video:', error);
            let detailMessage = 'Please check the URL and try again.';
            if (error instanceof Error) {
                detailMessage = error.message;
            } else if (typeof error === 'string') {
                detailMessage = error;
            }
            toast.error(`Failed to add YouTube video: ${detailMessage}`);
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
                <div 
                    className="youtube-input-container" 
                    style={{ 
                        padding: '5px 0',
                        animation: 'fadeIn 0.2s ease-in-out',
                        overflow: 'hidden',
                    }}
                >
                    <input
                        ref={youtubeInputRef}
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
                        disabled={isSending || !youtubeUrl.trim()}
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
                    <div className="add-content-wrapper" style={{ position: 'relative' }}>
                        <span
                            className="control-button add-content-button"
                            onClick={toggleAddMenu}
                            aria-label="Add content"
                            style={{ cursor: 'pointer', fontSize: '1.5em' }}
                        >
                            ➕
                        </span>
                        
                        {showAddMenu && (
                            <div 
                                className="add-content-menu"
                                style={{
                                    position: 'absolute',
                                    bottom: '60px',
                                    left: '0',
                                    backgroundColor: darkMode ? '#333' : 'white',
                                    border: darkMode ? '1px solid #555' : '1px solid #ccc',
                                    borderRadius: '8px',
                                    padding: '8px',
                                    zIndex: 10,
                                    boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                                    minWidth: '180px'
                                }}
                            >
                                <div 
                                    onClick={() => {
                                        document.getElementById('file-upload')?.click();
                                        setShowAddMenu(false);
                                    }}
                                    style={{
                                        padding: '10px 12px',
                                        cursor: 'pointer',
                                        borderRadius: '4px',
                                        marginBottom: '8px',
                                        display: 'flex',
                                        alignItems: 'center',
                                        backgroundColor: darkMode ? '#444' : '#f5f5f5',
                                    }}
                                >
                                    <span style={{ marginRight: '10px', fontSize: '1.2em' }}>📎</span> Upload Files
                                </div>
                                <div 
                                    onClick={() => {
                                        setShowYoutubeInput(true);
                                        setShowAddMenu(false);
                                    }}
                                    style={{
                                        padding: '10px 12px',
                                        cursor: 'pointer',
                                        borderRadius: '4px',
                                        display: 'flex',
                                        alignItems: 'center',
                                        backgroundColor: darkMode ? '#444' : '#f5f5f5',
                                    }}
                                >
                                    <span style={{ marginRight: '10px', fontSize: '1.2em' }}>📺</span> Add YouTube Video
                                </div>
                            </div>
                        )}
                    </div>
                    <input
                        id="file-upload"
                        type="file"
                        multiple
                        onChange={handleAttachment}
                        disabled={isSending}
                        aria-label="File upload input"
                        style={{ display: 'none' }} // Keep it hidden, we trigger it via our custom UI
                    />
                </div>
                <button
                    onClick={handleSendClick}
                    disabled={isSending || (!message.trim() && attachedFiles.length === 0)}
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
