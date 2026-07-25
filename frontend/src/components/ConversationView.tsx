import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Copy } from 'lucide-react';
import './ConversationView.css';
import { History, Message } from './types';
import { useUserContext } from '../UserContext';
import FileViewerComponent from './FileViewerComponent';
import { UploadedFile } from './types';
import { Button } from '@/components/ui/button';

interface ConversationViewProps {
    files: UploadedFile[];
    history: History | null;
    onCopy: (message: Message) => void;
}

const ConversationView: React.FC<ConversationViewProps> = ({ files, history, onCopy }) => {
    const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null);
    const [fileError, setFileError] = useState<string | null>(null);
    const { darkMode } = useUserContext();
    const messagesEndRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        scrollToBottom();
    }, [history]);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    const handleFileLinkClick = async (url: string) => {
        // Create a new URL object to parse the link
        const parsedUrl = new URL(url);
        const pathname = parsedUrl.pathname;

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
                    // Found the file in our system
                    setSelectedFile(matchingFile);
                    setFileError(null);
                    console.log(`Found file: ${matchingFile.file_name} (${matchingFile.id})`);
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
                    setSelectedFile({
                        id: -1, // Use negative ID for external files
                        file_name: (url as string).split('/').pop() || 'File',
                        file_url: url || '',
                        status: 'processed',
                        file_type: fileType // Use determined fileType
                    });
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
                    if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
                        return (
                            <a href="#" onClick={(e) => { e.preventDefault(); linkHandler && linkHandler(href); }}>
                                {children}
                            </a>
                        );
                    }
                    return <a href={href}>{children}</a>;
                },
            }}
        />
    );

    return (
        <div className={`conversation-view ${darkMode ? 'dark-mode' : ''}`}>
            {history?.messages.map((message) => {
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
                                <Button
                                    variant="ghost"
                                    size="icon-sm"
                                    onClick={() => handleCopy(message)}
                                    title="Copy answer"
                                >
                                    <Copy className="size-3.5" />
                                </Button>
                            )}
                        </div>
                    </div>
                );
            })}

            {fileError && <div className="error-message">{fileError}</div>}
            {selectedFile && (
                <div className="file-viewer-modal">
                    <div className="file-viewer-content">
                        <FileViewerComponent
                            file={selectedFile}
                            onClose={() => setSelectedFile(null)}
                            onError={handleFileError}
                            darkMode={darkMode}
                        />
                    </div>
                </div>
            )}
            <div ref={messagesEndRef} />
        </div>
    );
};

export default React.memo(ConversationView);
