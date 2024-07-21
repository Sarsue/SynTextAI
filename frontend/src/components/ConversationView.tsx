import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './ConversationView.css';
import { History, Message } from './types';
import { useDarkMode } from '../DarkModeContext';
import FileViewerComponent from './FileViewerComponent';

interface ConversationViewProps {
    history: History | null;
    onLike: (message: Message) => void;
    onDislike: (message: Message) => void;
    onCopy: (message: Message) => void;
}

const ConversationView: React.FC<ConversationViewProps> = ({ history, onLike, onDislike, onCopy }) => {
    const [selectedFile, setSelectedFile] = useState<string | null>(null); // Adjusted to store file URL string
    const [fileError, setFileError] = useState<string | null>(null);
    const { darkMode } = useDarkMode();
    const messagesEndRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        scrollToBottom();
    }, [history]);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    const handleFileLinkClick = (url: string) => {
        try {
            // Ensure the URL is valid
            const parsedUrl = new URL(url);
            console.log("Parsed URL:", parsedUrl.href);
            setSelectedFile(parsedUrl.toString());
            setFileError(null);
        } catch (error) {
            console.error('Error parsing URL:', error);
            setFileError('Invalid file URL.');
        }
    };

    const handleFileError = (error: string) => {
        console.error('File error:', error);
        setFileError(error);
        setSelectedFile(null);
    };

    const renderMarkdown = (markdown: string) => {
        console.log('Rendering markdown:', markdown);
        return (
            <ReactMarkdown
                children={markdown}
                remarkPlugins={[remarkGfm]}
                components={{
                    a: ({ href, children }) => {
                        console.log('Rendering link:', href);
                        if (href && href.startsWith('https://')) {
                            const regex = /https:\/\/(.*?)(?:\?page=(\d+))?/;
                            const match = regex.exec(href);
                            if (match && match.length >= 2) {
                                return (
                                    <a href="#" onClick={(e) => { e.preventDefault(); handleFileLinkClick(href); }}>
                                        {children}
                                    </a>
                                );
                            }
                        }
                        return <a href={href}>{children}</a>;
                    },
                }}
            />
        );
    };

    return (
        <div className={`conversation-view ${darkMode ? 'dark-mode' : ''}`}>
            {history?.messages.map((message) => (
                <div
                    key={message.id}
                    className={`chat-message ${message.sender === 'user' ? 'sent' : 'received'}`}
                >
                    <div className="message-timestamp">{message.timestamp}</div>
                    <div className="message-content">
                        {renderMarkdown(message.content)}
                    </div>
                    {message.sender === 'bot' && (
                        <div className="actions">
                            <button onClick={() => onLike(message)}>👍</button>
                            <button onClick={() => onDislike(message)}>👎</button>
                            <button onClick={() => onCopy(message)}>📋</button>
                        </div>
                    )}
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
