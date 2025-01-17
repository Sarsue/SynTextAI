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
    const [selectedFile, setSelectedFile] = useState<string | null>(null); // Adjusted to store file URL string
    const [fileError, setFileError] = useState<string | null>(null);
    const { darkMode } = useUserContext();
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
        return (
            <ReactMarkdown
                children={markdown}
                remarkPlugins={[remarkGfm]}
                components={{
                    a: ({ href, children }) => {
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
                    <div className="message-content">
                        {renderMarkdown(message.content)}
                    </div>
                    <div className="message-metadata">
                        <div className="message-timestamp">{message.timestamp}</div>
                        {message.sender === 'bot' && (
                            <div className="actions">
                                {/* <button
                                    className={`icon-button like-button ${message.liked ? 'active' : ''}`}
                                    onClick={() => onLike(message)}
                                >
                                    👍
                                </button>
                                <button
                                    className={`icon-button dislike-button ${message.disliked ? 'active' : ''}`}
                                    onClick={() => onDislike(message)}
                                >
                                    👎
                                </button> */}
                                <button className="icon-button copy-button" onClick={() => onCopy(message)}>
                                    📋
                                </button>
                            </div>
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