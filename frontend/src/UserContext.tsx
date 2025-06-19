import React, { createContext, useState, useEffect, useCallback, useRef, Dispatch, SetStateAction } from 'react';
import { UploadedFile, ProcessingStatus, PaginationState } from './components/types'; 
import { User as FirebaseUser } from 'firebase/auth';
import { KnownWebSocketMessage, WebSocketMessage, ChatMessagePayload, FileStatusUpdatePayload } from './types/websocketTypes';

// Define the type for UserSettings
interface UserSettings {
    comprehensionLevel: string;
    selectedLanguage: string;
}

// Define the type for SubscriptionData
interface SubscriptionData {
    subscription_status: string;
    card_last4?: string;
    card_brand?: string;
    card_exp_month?: string;
    card_exp_year?: string;
    trial_end?: string;
}

// Define the type for UserContext
interface UserContextType {
    user: FirebaseUser | null;
    setUser: (user: FirebaseUser | null) => void;
    darkMode: boolean;
    toggleDarkMode: () => void;
    setDarkMode: (darkMode: boolean) => void;
    userSettings: UserSettings;
    setUserSettings: (settings: UserSettings) => void;
    isPollingFiles: boolean;
    setIsPollingFiles: (isPollingFile: boolean) => void;
    isPollingMessages: boolean;
    setIsPollingMessages: (isPollingMsg: boolean) => void;
    subscriptionStatus: string | null;
    setSubscriptionStatus: (status: string | null) => void;
    fetchSubscriptionStatus: () => void;
    subscriptionData: SubscriptionData | null;
    setSubscriptionData: (data: SubscriptionData | null) => void;
    registerUserInBackend: (user: FirebaseUser) => Promise<void>;
    socket: WebSocket | null;
    initializeWebSocket: () => Promise<void>;
    disconnectWebSocket: () => void;

    // Centralized file state
    files: UploadedFile[];
    setFiles: Dispatch<SetStateAction<UploadedFile[]>>;
    filePagination: PaginationState;
    setFilePagination: Dispatch<SetStateAction<PaginationState>>;
    isLoadingFiles: boolean;
    setIsLoadingFiles: Dispatch<SetStateAction<boolean>>;
    fileError: string | null;
    setFileError: Dispatch<SetStateAction<string | null>>;
    loadUserFiles: (page: number, pageSize: number) => Promise<void>;
    deleteFileFromContext: (fileId: number) => Promise<void>; // Added for deleting files
    authLoading: boolean; // Added for initial user load status
}

// Create the UserContext with initial values
const UserContext = createContext<UserContextType>({
    user: null,
    setUser: () => { },
    darkMode: false,
    toggleDarkMode: () => { },
    setDarkMode: () => { },
    userSettings: {
        comprehensionLevel: 'Beginner',
        selectedLanguage: 'English',
    },
    setUserSettings: () => { },
    isPollingFiles: false,
    setIsPollingFiles: () => { },
    isPollingMessages: false,
    setIsPollingMessages: () => { },
    subscriptionStatus: null,
    setSubscriptionStatus: () => { },
    fetchSubscriptionStatus: () => { },
    subscriptionData: null,
    setSubscriptionData: () => { },
    registerUserInBackend: async () => { },
    socket: null,
    initializeWebSocket: async () => { },
    disconnectWebSocket: () => { },

    // Centralized file state initial values
    files: [],
    setFiles: () => {},
    filePagination: { page: 1, pageSize: 10, totalItems: 0 },
    setFilePagination: () => {},
    isLoadingFiles: false,
    setIsLoadingFiles: () => {},
    fileError: null,
    setFileError: () => {},
    loadUserFiles: async () => { },
    deleteFileFromContext: async () => { }, // Added for deleting files
    authLoading: true, // Default to true
});

// Define UserProvider component
export const UserProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [user, setUser] = useState<FirebaseUser | null>(null);
    const [darkMode, setDarkMode] = useState<boolean>(false);
    const [userSettings, setUserSettings] = useState<UserSettings>({
        comprehensionLevel: 'Beginner',
        selectedLanguage: 'English',
    });
    const [isPollingFiles, setIsPollingFiles] = useState<boolean>(false);
    const [isPollingMessages, setIsPollingMessages] = useState<boolean>(false);
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null);
    const [subscriptionData, setSubscriptionData] = useState<SubscriptionData | null>(null);
    const [socket, setSocket] = useState<WebSocket | null>(null);

    // Refs for WebSocket management
    const socketRef = useRef<WebSocket | null>(null);
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const reconnectAttempts = useRef<number>(0);

    // Constants for WebSocket reconnection
    const maxReconnectAttempts = 5;
    const initialReconnectDelay = 1000; // 1 second
    const maxReconnectDelay = 30000; // 30 seconds

    // Centralized file state
    const [files, setFiles] = useState<UploadedFile[]>([]);
    const [filePagination, setFilePagination] = useState<PaginationState>({ page: 1, pageSize: 10, totalItems: 0 });
    const [isLoadingFiles, setIsLoadingFiles] = useState<boolean>(false);
    const [isLoadingUser, setIsLoadingUser] = useState(true);
    const [fileError, setFileError] = useState<string | null>(null);

    const toggleDarkMode = () => {
        setDarkMode((prevMode) => !prevMode);
    };

    const fetchSubscriptionStatus = async () => {
        try {
            if (user) {
                const idToken = await user.getIdToken();
                const response = await fetch('/api/v1/subscriptions/status', {
                    method: 'GET',
                    headers: {
                        Authorization: `Bearer ${idToken}`,
                    },
                });

                if (!response.ok) {
                    throw new Error('Failed to fetch subscription status');
                }

                const data = await response.json();
                console.log(data);
                setSubscriptionStatus(data.subscription_status ?? null);
                setSubscriptionData(data);
            }
        } catch (error) {
            console.error('Error fetching subscription status:', error);
            setSubscriptionStatus(null);
            setSubscriptionData(null);
        }
    };

    const registerUserInBackend = async (user: FirebaseUser) => {
        try {
            const idToken = await user.getIdToken();
            const response = await fetch(`/api/v1/users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
            });

            if (!response.ok) {
                throw new Error(`Failed to register user: ${response.statusText}`);
            }

            console.log('User successfully registered in backend');
        } catch (error) {
            console.error('Error registering user in backend:', error);
        }
    };




    const cleanupWebSocket = useCallback((wsInstance: WebSocket | null, clearReconnect?: boolean) => {
        if (wsInstance) {
            wsInstance.onopen = null;
            wsInstance.onmessage = null;
            wsInstance.onerror = null;
            wsInstance.onclose = null;
            if (wsInstance.readyState === WebSocket.OPEN || wsInstance.readyState === WebSocket.CONNECTING) {
                wsInstance.close();
            }
        }
        if (socketRef.current === wsInstance) {
            socketRef.current = null;
            setSocket(null);
        }
        if (clearReconnect && reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
        }
    }, []);

    const initializeWebSocket = useCallback(async () => {
        if (!user || socketRef.current) {
            // If no user, or WebSocket already exists or is connecting, do nothing.
            return;
        }

        try {
            const token = await user.getIdToken();
            if (!token) {
                console.error("Failed to get auth token for WebSocket.");
                return;
            }

            const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
            const wsUrl = `${protocol}://${window.location.host}/ws/${user.uid}`;
            const ws = new WebSocket(wsUrl);
            socketRef.current = ws;
            setSocket(ws);

            ws.onopen = () => {
                console.log('WebSocket connected');
                reconnectAttempts.current = 0; // Reset on successful connection
                if (reconnectTimeoutRef.current) {
                    clearTimeout(reconnectTimeoutRef.current);
                    reconnectTimeoutRef.current = null;
                }
                ws.send(JSON.stringify({ type: 'auth', token }));
            };

            ws.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data) as KnownWebSocketMessage;
                    console.log('UserContext received WebSocket message:', message); // For debugging

                    if (!message || !message.event) return;

                    switch (message.event) {
                        case 'file_processed': {
                            const updatedFile = message.result as UploadedFile; 
                            console.log('UserContext: File processed event', updatedFile);
                            // Reload the current page of files to get the updated file list
                            loadUserFiles(filePagination.page, filePagination.pageSize);
                            // TODO: Consider if a toast notification is needed here (e.g., via a context method)
                            break;
                        }
                        case 'file_status_update':
                        case 'file_status_error': { 
                            const data = message.data as FileStatusUpdatePayload;
                            console.log(`UserContext: File status event (${message.event})`, data);
                            // Reload files if it's a key status change to ensure UI consistency
                            if (data.status === 'failed' || data.status === 'processed' || data.status === 'processing') {
                                loadUserFiles(filePagination.page, filePagination.pageSize);
                            }
                            // TODO: Consider toast for errors here
                            break;
                        }
                        case 'file_deleted': {
                            // Assuming message.data contains { file_id: number }
                            const data = message.data as { file_id: number }; 
                            console.log('UserContext: File deleted event for file_id:', data.file_id);
                            // Reload files to ensure pagination and total counts are correct
                            loadUserFiles(filePagination.page, filePagination.pageSize);
                            break;
                        }
                        case 'chat_message': {
                            // const chatPayload = message.payload as ChatMessagePayload;
                            // console.log('UserContext: Chat message received', chatPayload);
                            // Logic to handle chat messages if UserContext is responsible for them
                            break;
                        }
                        default:
                            console.log('UserContext: Received unhandled WebSocket event type:', message.event);
                    }
                } catch (error) {
                    console.error('UserContext: Error processing WebSocket message:', error);
                }
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                // Error event will typically be followed by a close event, which handles reconnection.
            };

            ws.onclose = (event) => {
                console.log(`WebSocket disconnected: Code ${event.code}, Reason: ${event.reason}`);
                cleanupWebSocket(ws, false); // Clean up this specific instance but don't clear pending reconnect timeout

                if (reconnectAttempts.current < maxReconnectAttempts) {
                    const delay = Math.min(
                        initialReconnectDelay * Math.pow(2, reconnectAttempts.current),
                        maxReconnectDelay
                    );
                    console.log(`Attempting to reconnect WebSocket in ${delay}ms... (Attempt ${reconnectAttempts.current + 1}/${maxReconnectAttempts})`);
                    
                    if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current); // Clear any existing timeout

                    reconnectTimeoutRef.current = setTimeout(() => {
                        reconnectAttempts.current += 1;
                        initializeWebSocket(); 
                    }, delay);
                } else {
                    console.error('Max WebSocket reconnection attempts reached.');
                }
            };
        } catch (error) {
            console.error('Error initializing WebSocket:', error);
            // This catch is for errors during the initial setup (e.g., getting token, new WebSocket())
            // Reconnection for this scenario might also be considered if appropriate
        }
    }, [user, cleanupWebSocket]); // Dependencies: user, cleanupWebSocket

    const disconnectWebSocket = useCallback(() => {
        console.log("Disconnecting WebSocket explicitly.");
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
        }
        reconnectAttempts.current = maxReconnectAttempts; // Prevent further auto-reconnection attempts
        if (socketRef.current) {
            cleanupWebSocket(socketRef.current, true);
        }
    }, [cleanupWebSocket]);

    // Effect to manage WebSocket connection based on user state
    useEffect(() => {
        if (user) {
            initializeWebSocket();
        } else {
            disconnectWebSocket();
        }

        // Cleanup function for when the component unmounts or user changes
        return () => {
            console.log("Cleaning up WebSocket due to UserContext unmount or user change.");
            disconnectWebSocket();
        };
    }, [user, initializeWebSocket, disconnectWebSocket]);

    // Internal API call helper
    const _callApiWithTokenInternal = async (url: string, method: string, body?: any) => {
        if (!user) {
            console.error('User not available for API call');
            return null;
        }
        try {
            const idToken = await user.getIdToken();
            if (!idToken) {
                console.error('User token not available');
                return null;
            }
            const headers: HeadersInit = { 'Authorization': `Bearer ${idToken}` };
            if (body && !(body instanceof FormData)) {
                headers['Content-Type'] = 'application/json';
            }
            const response = await fetch(url, { 
                method, 
                headers, 
                mode: 'cors', 
                credentials: 'include', 
                body: (body && body instanceof FormData) ? body : (body ? JSON.stringify(body) : undefined) 
            });
            return response;
        } catch (error) {
            console.error('Unexpected error calling API:', error);
            setFileError('Failed to communicate with server.');
            return null;
        }
    };

    const loadUserFiles = useCallback(async (page: number, pageSize: number) => {
        setIsLoadingFiles(true);
        setFileError(null);
        const url = `/api/v1/files?page=${page}&page_size=${pageSize}`;
        try {
            const response = await _callApiWithTokenInternal(url, 'GET');
            if (response && response.ok) {
                const data = await response.json();
                setFiles(data.items || []);
                setFilePagination({
                    page: data.page || page,
                    pageSize: data.page_size || pageSize,
                    totalItems: data.total || 0,
                });
            } else {
                const errorText = response ? await response.text().catch(() => response?.statusText || 'Unknown error') : 'No response';
                console.error('Failed to fetch files:', errorText);
                setFileError(`Failed to fetch files: ${errorText}`);
                setFiles([]); // Clear files on error
                setFilePagination({ page: 1, pageSize: 10, totalItems: 0 }); // Reset pagination
            }
        } catch (error) {
            console.error('Error in loadUserFiles:', error);
            setFileError('An unexpected error occurred while fetching files.');
            setFiles([]);
            setFilePagination({ page: 1, pageSize: 10, totalItems: 0 });
        }
        setIsLoadingFiles(false);
    }, [user, setFiles, setFilePagination, setIsLoadingFiles, setFileError]); // Added user and setters to dependency array

    // Effect to load files when user logs in
    useEffect(() => {
        if (user) {
            loadUserFiles(1, 10); // Load first page, 10 items per page
        }
    }, [user, loadUserFiles]); 

    const deleteFileFromContext = useCallback(async (fileId: number): Promise<void> => {
        setFileError(null);
        const url = `/api/v1/files/${fileId}`;
        try {
            const response = await _callApiWithTokenInternal(url, 'DELETE');
            if (response && response.ok) {
                setFiles(prevFiles => prevFiles.filter(file => file.id !== fileId));
                // Optionally, re-fetch files or adjust pagination if necessary
                // For now, just removing from the list. Consider if totalItems in pagination needs update.
                // loadUserFiles(filePagination.page, filePagination.pageSize); // to refresh and get correct total
            } else {
                const errorText = response ? await response.text().catch(() => response?.statusText || 'Unknown error') : 'No response';
                console.error(`Failed to delete file ${fileId}:`, errorText);
                setFileError(`Failed to delete file: ${errorText}`);
            }
        } catch (error) {
            console.error(`Error in deleteFileFromContext for file ${fileId}:`, error);
            setFileError('An unexpected error occurred while deleting the file.');
        }
    }, [user, setFiles, setFileError, _callApiWithTokenInternal, filePagination.page, filePagination.pageSize]); // Added dependencies

    // ... rest of your code
    return (
        <UserContext.Provider
            value={{
                user,
                setUser,
                darkMode,
                toggleDarkMode,
                setDarkMode,
                userSettings,
                setUserSettings,
                isPollingFiles,
                setIsPollingFiles,
                isPollingMessages,
                setIsPollingMessages,
                subscriptionStatus,
                setSubscriptionStatus,
                fetchSubscriptionStatus,
                subscriptionData,
                setSubscriptionData,
                registerUserInBackend,
                socket,
                initializeWebSocket,
                disconnectWebSocket,

                // Provide new file state and functions
                files,
                setFiles,
                filePagination,
                setFilePagination,
                isLoadingFiles,
                setIsLoadingFiles,
                fileError,
                setFileError,
                loadUserFiles,
                deleteFileFromContext, // Expose deleteFileFromContext
                authLoading: isLoadingUser, // Expose isLoadingUser as authLoading
            }}
        >
            {children}
        </UserContext.Provider>
    );
};

// Define useUserContext hook
export const useUserContext = (): UserContextType => {
    const context = React.useContext(UserContext);
    if (!context) {
        throw new Error('useUserContext must be used within a UserProvider');
    }
    return context;
};