import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Copy } from 'lucide-react';
import './ConversationView.css';
import { History, Message, MessageFeedback } from './types';
import AnswerFeedback from './AnswerFeedback';
import { useUserContext } from '../UserContext';
import FileViewerComponent from './FileViewerComponent';
import { UploadedFile } from './types';
import { Button } from '@/components/ui/button';

interface ConversationViewProps {
    files: UploadedFile[];
    history: History | null;
    /** A question has been queued and its answer has not arrived yet. */
    awaitingReply?: boolean;
    onCopy: (message: Message) => void;
    /** Records this caller's rating of one answer, or clears it when null. */
    onFeedbackChange: (messageId: number, feedback: MessageFeedback | null) => void;
}

const ConversationView: React.FC<ConversationViewProps> = ({ files, history, awaitingReply = false, onCopy, onFeedbackChange }) => {
    const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null);
    // The whole value of a citation is landing on the cited page. The click
    // handler parsed the URL but kept only the matched file record, whose
    // file_url has no fragment, so #page=36 was discarded before the viewer
    // ever saw it and every citation opened the document at page 1.
    const [selectedFragment, setSelectedFragment] = useState<string>('');
    const [fileError, setFileError] = useState<string | null>(null);
    const [isOpeningFile, setIsOpeningFile] = useState(false);
    const { darkMode, user } = useUserContext();
    const messagesEndRef = useRef<HTMLDivElement>(null);

    // Documents are private in storage, so file_url identifies a document but
    // does not grant access to it. Ask the API for a short-lived URL at the
    // moment of opening: authorization is then checked per view against the
    // document's workspace, and a citation copied out of an answer stays a
    // stable reference rather than becoming a working link to a private file.
    const resolveAccessUrl = async (fileId: number): Promise<string | null> => {
        if (!user) return null;
        try {
            const token = await user.getIdToken();
            const response = await fetch(`api/v1/files/${fileId}/access-url`, {
                headers: { Authorization: `Bearer ${token}` },
                mode: 'cors',
                credentials: 'include',
            });
            if (!response.ok) return null;
            const data = await response.json();
            return typeof data?.url === 'string' ? data.url : null;
        } catch {
            return null;
        }
    };

    useEffect(() => {
        // Also on awaitingReply, or the indicator appears below the fold and the
        // wait looks exactly as silent as it did before.
        scrollToBottom();
    }, [history, awaitingReply]);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    const handleFileLinkClick = async (url: string) => {
        // Create a new URL object to parse the link
        const parsedUrl = new URL(url);
        const pathname = parsedUrl.pathname;
        // #page=36 / #t=12.5 — the part that makes the citation worth clicking.
        const fragment = parsedUrl.hash;

        // Regular expression to check if the pathname ends with a valid file extension
        const fileExtensionPattern = /\.(pdf|jpg|jpeg|png|txt|doc|docx|html|ppt|xls|xlsx|csv|zip|mp4|mov)$/i;

        // Check if the pathname ends with a valid file extension
        const isFileLink = fileExtensionPattern.test(pathname);

        if (url.startsWith('http://') || url.startsWith('https://')) {
            if (isFileLink) {
                // Look for the file in our files array that matches this URL
                const matchingFile = files.find(file =>
                    url.includes(file.file_url)
                );

                if (matchingFile) {
                    // Found the file in our system. Its stored URL is an
                    // identity, not a readable link, so swap in a freshly
                    // signed one before handing it to the viewer.
                    setFileError(null);
                    setIsOpeningFile(true);
                    const accessUrl = await resolveAccessUrl(matchingFile.id);
                    setIsOpeningFile(false);
                    if (!accessUrl) {
                        setFileError(`Could not open ${matchingFile.file_name}. You may no longer have access to it.`);
                        return;
                    }
                    setSelectedFile({ ...matchingFile, file_url: accessUrl });
                    setSelectedFragment(fragment);
                } else {
                    // Determine the file type based on extension
                    let fileType: "audio" | "video" | "image" | "text" | "pdf" = "text"; // Default to text
                    const extension = pathname.split('.').pop()?.toLowerCase() || '';

                    if (["jpg", "jpeg", "png"].includes(extension)) {
                        fileType = "image";
                    } else if (["mp4", "mov"].includes(extension)) {
                        fileType = "video";
                    } else if (extension === "pdf") {
                        fileType = "pdf";
                    }

                    // If we can't find the file but it's still a valid URL, create a minimal file object
                    // Keep the fragment out of file_url and carry it separately:
                    // the viewer derives the file type from the URL's extension,
                    // and a trailing "#page=36" makes that read as "pdf#page=36".
                    setSelectedFile({
                        id: -1, // Use negative ID for external files
                        file_name: pathname.split('/').pop() || 'File',
                        file_url: `${parsedUrl.origin}${pathname}${parsedUrl.search}`,
                        status: 'processed',
                        file_type: fileType // Use determined fileType
                    });
                    setSelectedFragment(fragment);
                    console.log(`External file link: ${url}`);
                }
            } else {
                // Treat URLs without a file extension (or HTML links) as a webpage
                console.log(`User clicked webpage link: ${url}`);
                window.open(url, '_blank'); // Open in a new tab
            }
        }
    };

    const handleCopy = async (message: Message) => {
        onCopy(message);

    };

    const handleFileError = (error: string) => {
        setFileError(error);
        setSelectedFile(null);
    };

    const splitMessageAndSources = (content: string): { body: string; sources: string | null } => {
        const marker = '\n\n**Sources:**';
        const idx = content.indexOf(marker);
        if (idx === -1) return { body: content, sources: null };
        return {
            body: content.slice(0, idx),
            sources: content.slice(idx + marker.length).trim(),
        };
    };

    const renderMarkdown = (markdown: string, linkHandler?: (href: string) => void) => (
        <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            children={markdown}
            components={{
                a: ({ href, children }) => {
                    // Citations carry a #page=N fragment and are the whole point
                    // of the product, so they must behave like real links.
                    // Rewriting href to "#" and relying on a handler meant the
                    // browser showed localhost/# and, when no handler was
                    // wired, clicking did nothing at all.
                    if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
                        return (
                            <a
                                href={href}
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={(e) => {
                                    // Let an in-app viewer take over when one is
                                    // available, but never swallow the click.
                                    if (linkHandler) {
                                        e.preventDefault();
                                        linkHandler(href);
                                    }
                                }}
                            >
                                {children}
                            </a>
                        );
                    }
                    return <a href={href}>{children}</a>;
                },
            }}
        />
    );

    // A conversation with no messages renders nothing, deliberately. The blank
    // screen this used to produce was a crash, not an empty state: the API
    // returned a new conversation without a messages array and
    // history.messages.map threw, unmounting the tree. That is fixed at the
    // source and guarded here; the panel staying empty is the intended look.
    const messages = history?.messages ?? [];

    return (
        <div className={`conversation-view ${darkMode ? 'dark-mode' : ''}`}>
            {messages.map((message) => {
                const isBot = message.sender === 'bot';
                const { body, sources } = isBot
                    ? splitMessageAndSources(message.content)
                    : { body: message.content, sources: null };

                return (
                    <div
                        key={message.id}
                        className={`chat-message ${message.sender === 'user' ? 'sent' : 'received'}`}
                    >
                        <div className="message-content">
                            {renderMarkdown(body, handleFileLinkClick)}
                        </div>
                        {sources && (
                            <div className="citation-box">
                                <div className="citation-label">Sources</div>
                                {renderMarkdown(sources, handleFileLinkClick)}
                            </div>
                        )}
                        <div className="message-metadata">
                            <div className="message-timestamp">{message.timestamp}</div>
                            {isBot && (
                                <>
                                    <Button
                                        variant="ghost"
                                        size="icon-sm"
                                        onClick={() => handleCopy(message)}
                                        title="Copy answer"
                                    >
                                        <Copy className="size-3.5" />
                                    </Button>
                                    {/* Only on answers, and only on ones that
                                        have been saved: an optimistic message
                                        still carries a Date.now() placeholder
                                        id, and rating that would 404. */}
                                    {message.id < 1e12 && (
                                        <AnswerFeedback
                                            messageId={message.id}
                                            feedback={message.feedback}
                                            onChange={onFeedbackChange}
                                        />
                                    )}
                                </>
                            )}
                        </div>
                    </div>
                );
            })}

            {isOpeningFile && (
                <div className="opening-file-notice" aria-live="polite">Opening document…</div>
            )}

            {awaitingReply && (
                <div className="chat-message received thinking-message" aria-live="polite">
                    <div className="thinking-indicator">
                        <span className="thinking-dots" aria-hidden="true">
                            <i /><i /><i />
                        </span>
                        <span className="thinking-label">Reading your documents…</span>
                    </div>
                </div>
            )}

            {fileError && <div className="error-message">{fileError}</div>}
            {selectedFile && (
                // FileViewerComponent renders its own Dialog, overlay included,
                // so it must not be wrapped in another overlay.
                <FileViewerComponent
                    file={selectedFile}
                    fragment={selectedFragment}
                    onClose={() => {
                        setSelectedFile(null);
                        setSelectedFragment('');
                    }}
                    onError={handleFileError}
                    darkMode={darkMode}
                />
            )}
            <div ref={messagesEndRef} />
        </div>
    );
};

export default React.memo(ConversationView);
