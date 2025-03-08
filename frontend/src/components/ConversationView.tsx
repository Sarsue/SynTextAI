import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './ConversationView.css';
import { History, Message } from './types';
import { useUserContext } from '../UserContext';
import FileViewerComponent from './FileViewerComponent';


interface ConversationViewProps {
    history: History | null;
    onCopy: (message: Message) => void;
}

const ConversationView: React.FC<ConversationViewProps> = ({ history, onCopy }) => {
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
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
        const fileExtensionPattern = /\.(pdf|jpg|jpeg|png|txt|doc|docx|html|ppt|xls|xlsx|csv|zip)$/i;

        // Check if the pathname ends with a valid file extension
        const isFileLink = fileExtensionPattern.test(pathname);

        if (url.startsWith('http://') || url.startsWith('https://')) {
            if (isFileLink) {
                // Handle as a file link
                setSelectedFile(url);
                setFileError(null);
                console.log(`User clicked file link: ${url}`);
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

    const renderMarkdown = (markdown: string) => (
        <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            children={markdown}
            components={{
                a: ({ href, children }) => {
                    if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
                        return (
                            <a href="#" onClick={(e) => { e.preventDefault(); handleFileLinkClick(href); }}>
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
            {history?.messages.map((message) => (
                <div
                    key={message.id}
                    className={`chat-message ${message.sender === 'user' ? 'sent' : 'received'}`}
                >
                    <div className="message-content">
                        {renderMarkdown(message.content)}
                    </div>
                    <div className="message-metadata">
                        <div className="message-timestamp">{message.timestamp}</div>
                        {message.sender === 'bot' && (
                            <button
                                className="icon-button copy-button"
                                onClick={() => handleCopy(message)}
                            >
                                📋
                            </button>
                        )}
                    </div>
                </div>
            ))}

            {fileError && <div className="error-message">{fileError}</div>}
            {selectedFile && (
                <div className="file-viewer-modal">
                    <div className="file-viewer-content">
                        <FileViewerComponent
                            fileUrl={selectedFile}
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

export default ConversationView;
