import React, { useState } from 'react';
import { Paperclip, X } from 'lucide-react';

import { useUserContext } from '../UserContext';
import DriveImport from './DriveImport';
import { useToast } from '../contexts/ToastContext';
import './InputArea.css';
import { Button, buttonVariants } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

export type ComposerMode = 'ask' | 'find' | 'write';

interface InputAreaProps {
    onSend: (message: string, files: File[]) => Promise<void>;
    /** Ticked in write mode to hand the conversation to the document.
     *  Lives here rather than in a sidebar because it refers to the
     *  conversation on screen, and a control describing something a person is
     *  looking at belongs next to it. */
    useConversation?: boolean;
    onUseConversationChange?: (value: boolean) => void;
    /** Whether there is a conversation to offer at all. */
    hasConversation?: boolean;
    /** Seconds the current write has been running. Writing a document takes
     *  meaningfully longer than answering, and a button that says how long it
     *  has been going is the difference between waiting and wondering. */
    elapsedSeconds?: number;
    isSending: boolean;
    onContentAdded: () => Promise<void>;
    mode: ComposerMode;
    onModeChange: (mode: ComposerMode) => void;
    /** Where an imported document lands. Null means no workspace is chosen. */
    workspaceId: number | null;
}

const InputArea: React.FC<InputAreaProps> = ({
    onSend,
    useConversation = false,
    onUseConversationChange,
    hasConversation = false,
    elapsedSeconds = 0,
    isSending,
    onContentAdded,
    mode,
    onModeChange,
    workspaceId,
}) => {
    const [message, setMessage] = useState('');
    const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
    const { darkMode, orgContext } = useUserContext();
    // Managing documents is an organization permission.
    //
    // Defaults to *not* allowed while the context loads. It defaulted to
    // allowed, to spare an owner a flicker, which meant a read-only member was
    // offered the attach control in that window, picked a file, and got a 403
    // — an action the product had shown them and then refused. An owner seeing
    // the control appear a moment late is the cheaper mistake.
    const canUpload = orgContext?.can_manage_documents ?? false;
    const { addToast } = useToast();

    // Exactly what the processor factory can read: pdf, docx, txt and md.
    //
    // Not application/msword. The legacy binary .doc format has no processor,
    // python-docx reads only .docx, so the factory maps 'doc' to None and a
    // .doc upload was accepted, stored, and never became searchable. Offering
    // a format we cannot read makes the failure the customer's to discover.
    //
    // Matched on extension as well as MIME type because browsers disagree about
    // markdown: Chrome reports text/markdown, some report text/plain and some
    // report an empty string, so a MIME check alone rejects a .md file the
    // backend handles perfectly well.
    const SUPPORTED_MIME = [
        'application/pdf',
        'text/plain',
        'text/markdown',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    ];
    const SUPPORTED_EXT = ['pdf', 'docx', 'txt', 'md'];

    const isFileSupported = (file: File): boolean => {
        if (SUPPORTED_MIME.includes(file.type)) return true;
        const ext = file.name.split('.').pop()?.toLowerCase();
        return !!ext && SUPPORTED_EXT.includes(ext);
    };

    const isFinding = mode === 'find';
    const isWriting = mode === 'write';
    // Both need words and neither takes an attachment: there is nothing to
    // search for, or to write from, in a file that is not in the workspace yet.
    const textOnly = isFinding || isWriting;

    const handleSendClick = () => {
        // Finding needs words. There is nothing to search for in an attachment,
        // and silently uploading it instead would be a different action from
        // the one the button offered.
        if (textOnly) {
            if (!message.trim()) {
                addToast(
                    isWriting ? 'Describe the document you want.' : 'Type what you want to find.',
                    'info',
                );
                return;
            }
            if (isWriting) {
                // Cleared on submit, unlike a search: a document is a one-off
                // request, and leaving the description behind would suggest
                // pressing again does something different.
                const text = message;
                onSend(text, []).then(() => setMessage(''));
                return;
            }
            onSend(message, []).then(() => {
                // The query stays in the box on purpose, unlike a sent message:
                // searching is iterative, and the usual next move is to change
                // one word rather than start again.
            });
            return;
        }

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
            addToast('Unsupported file type. Please upload PDF, DOCX, TXT or MD files.', 'error');
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
            {/* Three things the same box can do, named by what the person
                wants rather than by how it works: an answer, the page it is on,
                or a document. Writing used to have its own textarea and its own
                button in the sidebar, which was a second place to type for the
                same kind of act. A radiogroup rather than buttons, so a screen
                reader says which is selected. */}
            <div className="composer-modes" role="radiogroup" aria-label="What to do">
                <button
                    type="button"
                    role="radio"
                    aria-checked={mode === 'ask'}
                    className={cn('composer-mode', mode === 'ask' && 'is-active')}
                    onClick={() => onModeChange('ask')}
                >
                    Ask
                </button>
                <button
                    type="button"
                    role="radio"
                    aria-checked={isFinding}
                    className={cn('composer-mode', isFinding && 'is-active')}
                    onClick={() => onModeChange('find')}
                >
                    Find
                </button>
                {/* Writing a document into a workspace is adding to it, and the
                    server holds it to the same capability as an upload. Staff
                    would see the mode, type a request and get a 403. */}
                {canUpload && (
                    <button
                        type="button"
                        role="radio"
                        aria-checked={isWriting}
                        className={cn('composer-mode', isWriting && 'is-active')}
                        onClick={() => onModeChange('write')}
                    >
                        Write
                    </button>
                )}
            </div>
            <Textarea
                className="composer-input"
                placeholder={
                    isWriting
                        ? 'An onboarding checklist for a new hygienist, from our policies...'
                        : isFinding
                            ? 'Find a passage in your documents...'
                            : 'Ask a question about your documents...'
                }
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isSending}
                aria-label={
                    isWriting ? 'Describe the document' : isFinding ? 'Search input' : 'Message input'
                }
            />
            {isWriting && (
                <div className="composer-write-note">
                    {hasConversation && (
                        <label className="composer-use-conversation">
                            <input
                                type="checkbox"
                                checked={useConversation}
                                onChange={(e) => onUseConversationChange?.(e.target.checked)}
                                disabled={isSending}
                            />
                            Use this conversation too
                        </label>
                    )}
                    <span>
                        Written from this workspace's documents
                        {useConversation && hasConversation ? ' and the conversation above' : ' only'}.
                        It answers no questions until you add it to the knowledge base.
                    </span>
                </div>
            )}
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
                                accept=".pdf,.docx,.txt,.md"
                                onChange={handleAttachment}
                                disabled={isSending}
                                aria-label="File upload"
                                style={{ display: 'none' }}
                            />
                            {/* Beside the paperclip, because both answer the
                                same question: how do I get a document in.
                                Same permission gate, for the same reason. */}
                            <DriveImport
                                compact
                                workspaceId={workspaceId}
                                onImported={onContentAdded}
                            />
                        </>
                    )}
                </div>
                <Button
                    onClick={handleSendClick}
                    disabled={
                        isSending ||
                        (textOnly
                            ? !message.trim()
                            : !message.trim() && attachedFiles.length === 0)
                    }
                    aria-label={
                        isWriting ? 'Write the document'
                            : isFinding ? 'Search documents' : 'Send message'
                    }
                    className="send-button"
                >
                    {isWriting
                        // Writing takes tens of seconds, so the button says how
                        // long it has been going rather than sitting on a word.
                        ? isSending ? `Writing, ${elapsedSeconds}s` : 'Write it'
                        : isFinding
                            ? isSending ? 'Searching...' : 'Find'
                            : isSending ? 'Sending...' : 'Send'}
                </Button>
            </div>
        </div>
    );
};

export default InputArea;
