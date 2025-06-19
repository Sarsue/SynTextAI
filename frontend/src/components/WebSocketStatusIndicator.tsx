import React from 'react';
import { useUserContext } from '../UserContext';
import './WebSocketStatusIndicator.css';

const WebSocketStatusIndicator: React.FC = () => {
    const { webSocketStatus } = useUserContext();

    const getStatusInfo = () => {
        switch (webSocketStatus) {
            case 'connected':
                return {
                    className: 'connected',
                    title: 'Real-time connection active',
                    text: 'Connected'
                };
            case 'connecting':
                return {
                    className: 'connecting',
                    title: 'Connecting to real-time service...',
                    text: 'Connecting'
                };
            case 'reconnecting':
                return {
                    className: 'reconnecting',
                    title: 'Connection lost. Attempting to reconnect...',
                    text: 'Reconnecting'
                };
            case 'disconnected':
                return {
                    className: 'disconnected',
                    title: 'Real-time connection lost. Please refresh the page.',
                    text: 'Disconnected'
                };
            default:
                return {
                    className: 'disconnected',
                    title: 'Unknown connection status',
                    text: 'Unknown'
                };
        }
    };

    const { className, title, text } = getStatusInfo();

    return (
        <div className="websocket-status-container" title={title}>
            <div className={`status-indicator ${className}`}></div>
            <span className="status-text">{text}</span>
        </div>
    );
};

export default WebSocketStatusIndicator;
