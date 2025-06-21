// This file will contain TypeScript interfaces for WebSocket messages
import { ProcessingStatus } from '../components/types';

// General structure for all WebSocket messages
export interface WebSocketMessage<T = any> {
    event: string;      // Type of the event, e.g., 'file_status_update', 'file_processed', 'message_received'
    data?: T;           // Payload of the message, type varies by event
    status?: 'success' | 'error'; // Optional status, often used with 'file_processed' or 'message_received'
    error?: string;     // Error message if status is 'error'
    result?: any;       // Result data, often used with 'file_processed'
    // Add any other common fields observed in messages
}

// Specific payload for 'auth' message sent by client
export interface AuthMessagePayload {
    type: 'auth';
    token: string;
}

// Specific payload for 'file_status_update' event
export interface FileStatusUpdatePayload {
    file_id: number;
    status: ProcessingStatus; // Use the specific type from components/types.ts
    error_message?: string | null;
    // Add other relevant fields like progress if available
}

// Specific payload for 'file_processed' event (when backend signals completion/failure of a file)
// This might be part of the 'result' or 'data' field depending on current implementation
export interface FileProcessedResult {
    file_id: number;
    filename: string;
    // other details about the processed file
}

// Example of using the generic WebSocketMessage with a specific payload
export type FileStatusUpdateMessage = WebSocketMessage<FileStatusUpdatePayload>;
export type FileProcessedMessage = WebSocketMessage; // Assuming result field is used as per current structure

// Add more specific message types as needed, for example for chat messages:
export interface ChatMessagePayload {
    chat_history_id: number;
    message_id: string;
    sender: 'user' | 'bot';
    text: string;
    timestamp: string; // ISO date string
    // any other chat message fields
}

export type ChatReceivedMessage = WebSocketMessage<ChatMessagePayload>;

// You can also define an overarching type for any known incoming WebSocket event for stricter handling
export type KnownWebSocketMessage =
    | FileStatusUpdateMessage
    | FileProcessedMessage
    | ChatReceivedMessage
    | WebSocketMessage<'auth_ack'> // Example for an auth acknowledgement
    | WebSocketMessage<'error_notification'>; // Example for a generic error pushed from backend

console.log('WebSocket types defined.'); // Placeholder to ensure file is not empty if all are comments
