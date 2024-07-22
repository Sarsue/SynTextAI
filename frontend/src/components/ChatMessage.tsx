// ChatMessage.tsx
import React from 'react';

interface Message {
    id: number;
    content: string;
    sender: string;
}

interface ChatMessageProps {
    message: Message;
}

const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
    return (
        <div className={`chat-message ${message.sender}`}>
            {message.content}
        </div>
    );
};

export default ChatMessage;
