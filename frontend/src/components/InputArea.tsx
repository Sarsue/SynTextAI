import React, { useState } from 'react';
import { Paperclip, X } from 'lucide-react';

import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';
import './InputArea.css';
import { Button, buttonVariants } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

interface InputAreaProps {
    onSend: (message: string, files: File[]) => Promise<void>;
    isSending: boolean;
    onContentAdded: () => Promise<void>;
}

const InputArea: React.FC<InputAreaProps> = ({ onSend, isSending, onContentAdded }) => {
    const [message, setMessage] = useState('');
    const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
    const { darkMode, orgContext } = useUserContext();
    // Managing documents is an organization permission. Default to allowed
    // while the context loads so an owner never sees the control flicker away.
    const canUpload = orgContext ? orgContext.can_manage_documents : true;
    const { addToast } = useToast();

    const isFileSupported = (file: File): boolean => {
        const supportedTypes = [
            'application/pdf',
            'text/plain',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/msword',
        ];
        return supportedTypes.includes(file.type);
    };

    const handleSendClick = () => {
        if (!message.trim() && attachedFiles.length === 0) {
            addToast('Please type a message or attach a file.', 'info');
            return;
        }

        const filesToSend = attachedFiles.filter(isFileSupported);

        if (filesToSend.length !== attachedFiles.length && attachedFiles.length > 0) {
            addToast('Some files have unsupported types and will be skipped.', 'warning');
        }

        if (filesToSend.length > 10) {
            addToast('Maximum of 10 files per upload.', 'error');
            return;
        }

        if (message.trim() || filesToSend.length > 0) {
            onSend(message, filesToSend).then(() => {
                setMessage('');
                setAttachedFiles([]);
            });
        }
    };

    const handleAttachment = (e: React.ChangeEvent<HTMLInputElement>) => {
        const selected = Array.from(e.target.files || []);
        if (selected.length === 0) return;

        const supported = selected.filter(isFileSupported);

        if (supported.length === 0) {
            addToast('Unsupported file type. Please upload PDF, DOCX, or TXT files.', 'error');
        } else {
            if (selected.length > supported.length) {
                addToast(`${selected.length - supported.length} file(s) skipped — unsupported type.`, 'info');
            }
            setAttachedFiles(prev => {
                const combined = [...prev, ...supported];
                if (combined.length > 10) {
                    const space = 10 - prev.length;
                    addToast(`Adding first ${space} file(s). Maximum is 10.`, 'warning');
                    return [...prev, ...supported.slice(0, space)];
                }
                return combined;
            });
        }
        e.target.value = '';
    };

    const handleRemoveFile = (fileToRemove: File) => {
        setAttachedFiles(prev => prev.filter(f => f !== fileToRemove));
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendClick();
        }
    };

    return (
        <div className={`input-area ${darkMode ? 'dark-mode' : ''}`}>
            <Textarea
                className="composer-input"
                placeholder="Ask a question about your documents..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isSending}
                aria-label="Message input"
            />
            {attachedFiles.length > 0 && (
                <div className="attached-files">
                    {attachedFiles.map((file, index) => (
                        <div key={index} className="attached-file">
                            <span>{file.name}</span>
                            <Button variant="ghost" size="icon-sm" onClick={() => handleRemoveFile(file)} aria-label="Remove file">
                                <X className="size-3.5" />
                            </Button>
                        </div>
                    ))}
                </div>
            )}
            <div className="input-controls">
                <div className="left-controls">
                    {/* Uploading is owner-only on the backend, so staff would
                        attach a file, hit send and get a 403. Hide the control
                        rather than offer an action that cannot succeed. */}
                    {canUpload && (
                        <>
                            <label
                                htmlFor="file-upload"
                                title="Attach files (PDF, DOCX, TXT)"
                                className={cn(buttonVariants({ variant: 'ghost', size: 'icon' }), 'cursor-pointer')}
                            >
                                <Paperclip className="size-4" />
                            </label>
                            <input
                                id="file-upload"
                                type="file"
                                multiple
                                accept=".pdf,.docx,.doc,.txt"
                                onChange={handleAttachment}
                                disabled={isSending}
                                aria-label="File upload"
                                style={{ display: 'none' }}
                            />
                        </>
                    )}
                </div>
                <Button
                    onClick={handleSendClick}
                    disabled={isSending || (!message.trim() && attachedFiles.length === 0)}
                    aria-label="Send message"
                    className="send-button"
                >
                    {isSending ? 'Sending...' : 'Send'}
                </Button>
            </div>
        </div>
    );
};

export default InputArea;
