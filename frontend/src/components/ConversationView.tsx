import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './ConversationView.css';
import { History, Message } from './types';
import { useUserContext } from '../UserContext';
import FileViewerComponent from './FileViewerComponent';
import { LogUIActions } from '../apiUtils';

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
        setSelectedFile(url);
        setFileError(null);

        // Log file link click
        const logUrl = 'api/v1/logs';
        await LogUIActions(logUrl, 'POST', `User clicked file link: ${url}`, 'info');
        console.log(`User clicked file link: ${url}`);
    };

    const handleCopy = async (message: Message) => {
        onCopy(message);

        // Log message copy
        // const logUrl = 'api/v1/logs';
        // await LogUIActions(logUrl, 'POST', `User copied message: ${message.content}`, 'info');
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
                    if (href && href.startsWith('https://')) {
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
