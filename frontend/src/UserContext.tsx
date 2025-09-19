import React, {
    createContext,
    useState,
    useEffect,
    useCallback,
    useRef,
    Dispatch,
    SetStateAction,
    ReactNode,
    useContext,
} from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { UploadedFile, PaginationState } from './components/types';
import { useToast } from './contexts/ToastContext';
import { User as FirebaseUser, getAuth, onAuthStateChanged } from 'firebase/auth';
import { KnownWebSocketMessage, FileStatusUpdatePayload } from './types/websocketTypes';

export type WebSocketStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

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
    card_exp_month?: number;
    card_exp_year?: number;
    trial_end?: string;  // ISO 8601 format
    message?: string;    // Optional success/status message
    error?: string;      // Present if status is 'error'
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
    webSocketStatus: WebSocketStatus;

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
    deleteFileFromContext: (fileId: number) => Promise<void>;
    authLoading: boolean;
}

// Create the UserContext with initial values
const UserContext = createContext<UserContextType>({} as UserContextType);

const MAX_RECONNECT_ATTEMPTS = 5;

export const UserProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const { addToast } = useToast();
    const [user, setUser] = useState<FirebaseUser | null>(null);
    const [authLoading, setAuthLoading] = useState(true);
    const [darkMode, setDarkMode] = useState<boolean>(false);
    const [userSettings, setUserSettings] = useState<UserSettings>({ comprehensionLevel: 'Beginner', selectedLanguage: 'English' });
    const [isPollingMessages, setIsPollingMessages] = useState<boolean>(false);
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null);
    const [subscriptionData, setSubscriptionData] = useState<SubscriptionData | null>(null);
    // File state
    const [files, setFiles] = useState<UploadedFile[]>([]);
    
    // WebSocket state
    const [socket, setSocket] = useState<WebSocket | null>(null);
    const [filePagination, setFilePagination] = useState<PaginationState>({ page: 1, pageSize: 10, totalItems: 0 });
    const [isLoadingFiles, setIsLoadingFiles] = useState<boolean>(false);
    const [fileError, setFileError] = useState<string | null>(null);

// WebSocket message handler
    const handleWebSocketMessage = useCallback((message: KnownWebSocketMessage) => {
        try {
            switch (message.event) {
                case 'file_processed': {
                    const updatedFile = message.result as UploadedFile;
                    setFiles(prevFiles => 
                        prevFiles.map(f => f.id === updatedFile.id ? updatedFile : f)
                    );
                    addToast(`File "${updatedFile.file_name}" has been processed.`, 'success');
                    break;
                }

                case 'file_status_update': {
                    const data = message.data as FileStatusUpdatePayload;
                    setFiles(prevFiles =>
                        prevFiles.map(file => {
                            if (file.id === data.file_id) {
                                return {
                                    ...file,
                                    status: data.status,
                                    error_message: data.error_message,
                                    progress: data.progress ?? file.progress
                                };
                            }
                            return file;
                        })
                    );

                    if (data.status === 'failed') {
                        addToast(`Error processing file: ${data.error_message || 'Unknown error'}`, 'error');
                    }
                    break;
                }

                case 'file_status_error': {
                    const data = message.data as FileStatusUpdatePayload;
                    setFiles(prevFiles =>
                        prevFiles.map(file => {
                            if (file.id === data.file_id) {
                                return {
                                    ...file,
                                    status: 'failed',
                                    error_message: data.error_message || 'An unknown error occurred',
                                    progress: 0 // Reset progress on error
                                };
                            }
                            return file;
                        })
                    );
                    addToast(`Error processing file: ${data.error_message || 'Unknown error'}`, 'error');
                    break;
                }

                case 'file_deleted': {
                    const data = message.data as { file_id: number };
                    setFiles(prevFiles => {
                        const updatedFiles = prevFiles.filter(f => f.id !== data.file_id);
                        // If the file was being processed, show a message
                        const deletedFile = prevFiles.find(f => f.id === data.file_id);
                        if (deletedFile?.status === 'processing') {
                            addToast('File processing was cancelled.', 'info');
                        } else {
                            addToast('File was deleted.', 'info');
                        }
                        return updatedFiles;
                    });
                    break;
                }
            }
        } catch (error) {
            console.error('Error handling WebSocket message:', error);
        }
    }, [addToast]);

    // Initialize WebSocket
    const { status: webSocketStatus, send: sendWebSocketMessage } = useWebSocket(
        user?.uid,
        handleWebSocketMessage,
        (status) => {
            console.log('WebSocket status changed:', status);
            if (status === 'connected') {
                addToast('Real-time connection established.', 'success');
            } else if (status === 'reconnecting') {
                addToast('Connection lost. Reconnecting...', 'warning');
            } else if (status === 'disconnected' && user) {
                addToast('Could not establish real-time connection. Some features may be limited.', 'error');
            }
        }
    );

    // Keep socket in sync with the context
    useEffect(() => {
        setSocket(sendWebSocketMessage ? { send: sendWebSocketMessage } as unknown as WebSocket : null);
    }, [sendWebSocketMessage]);

    const toggleDarkMode = () => setDarkMode((prev) => !prev);

    const _callApiWithTokenInternal = useCallback(async (url: string, method: string, body?: any) => {
        if (!user) return null;
        try {
            const idToken = await user.getIdToken();
            const headers: HeadersInit = { 'Authorization': `Bearer ${idToken}` };
            if (body && !(body instanceof FormData)) {
                headers['Content-Type'] = 'application/json';
            }
            const response = await fetch(url, {
                method,
                headers,
                body: (body && body instanceof FormData) ? body : (body ? JSON.stringify(body) : undefined)
            });
            if (!response.ok) {
                 const errorText = await response.text().catch(() => 'Unknown API error');
                 console.error('API call failed:', errorText);
                 setFileError(`API Error: ${errorText}`);
                 return null;
            }
            return response;
        } catch (error) {
            console.error('API call exception:', error);
            setFileError('Failed to communicate with the server.');
            return null;
        }
    }, [user]);

    const loadUserFiles = useCallback(async (page: number, pageSize: number) => {
        setIsLoadingFiles(true);
        setFileError(null);
        const url = `/api/v1/files?page=${page}&page_size=${pageSize}`;
        try {
            const response = await _callApiWithTokenInternal(url, 'GET');
            if (response?.ok) {
                const data = await response.json();
                console.log('Server response for files:', JSON.stringify(data, null, 2));
                setFiles(data.items || []);
                setFilePagination({ page: data.page, pageSize: data.page_size, totalItems: data.total });
            } else {
                setFileError('Failed to fetch files.');
            }
        } catch (error) {
            setFileError('An unexpected error occurred while fetching files.');
        } finally {
            setIsLoadingFiles(false);
        }
    }, [_callApiWithTokenInternal]);
    
    // Initialize WebSocket when user is authenticated
    useEffect(() => {
        // The useWebSocket hook handles connection management automatically
        return () => {
            // Cleanup is handled by the useWebSocket hook
        };
    }, [user]);

    // Initialize WebSocket function for backward compatibility
    const initializeWebSocket = useCallback(async () => {
        // No-op as connection is managed by useWebSocket hook
        console.log('WebSocket initialization is now handled automatically');
    }, []);

    // Disconnect WebSocket function for backward compatibility
    const disconnectWebSocket = useCallback(() => {
        // No-op as disconnection is managed by useWebSocket hook
        console.log('WebSocket disconnection is now handled automatically');
    }, []);

    const fetchSubscriptionStatus = useCallback(async () => {
        if (!user) {
            setSubscriptionStatus('none');
            setSubscriptionData({ subscription_status: 'none' });
            return;
        }
        try {
            const response = await _callApiWithTokenInternal('/api/v1/subscriptions/status', 'GET');
            if (response?.ok) {
                const data = await response.json();
                console.log('Subscription status data:', data);
                const status = data.status || data.subscription_status || 'none';
                setSubscriptionStatus(status);
                setSubscriptionData({
                    subscription_status: status,
                    card_last4: data.card_last4,
                    card_brand: data.card_brand,
                    card_exp_month: data.card_exp_month,
                    card_exp_year: data.card_exp_year,
                    trial_end: data.trial_end,
                    message: data.message,
                    error: data.error
                });
            } else {
                console.error('Failed to fetch subscription status');
                setSubscriptionStatus('none');
                setSubscriptionData({ subscription_status: 'none' });
            }
        } catch (error) {
            console.error('Error fetching subscription status:', error);
            setSubscriptionStatus('none');
            setSubscriptionData({ subscription_status: 'none' });
        }
    }, [user, _callApiWithTokenInternal]);

    const registerUserInBackend = useCallback(async (fbUser: FirebaseUser) => {
        const idToken = await fbUser.getIdToken();
        const response = await fetch(`/api/v1/users`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${idToken}`,
            },
            body: JSON.stringify({ 'firebase_uid': fbUser.uid, 'email': fbUser.email })
        });
        if (!response.ok) {
            console.error('Failed to register user in backend');
        }
    }, []);

    const deleteFileFromContext = useCallback(async (fileId: number) => {
        const url = `/api/v1/files/${fileId}`;
        const response = await _callApiWithTokenInternal(url, 'DELETE');
        if (response?.ok) {
            addToast('File deleted successfully!', 'success');
            // After deletion, reload the files to get an updated list from the server
            await loadUserFiles(filePagination.page, filePagination.pageSize);
        } else {
            addToast('Failed to delete file.', 'error');
        }
    }, [_callApiWithTokenInternal, addToast, loadUserFiles, filePagination.page, filePagination.pageSize]);
    
    useEffect(() => {
        const auth = getAuth();
        const unsubscribe = onAuthStateChanged(auth, async (fbUser) => {
            setAuthLoading(true);
            if (fbUser) {
                await registerUserInBackend(fbUser);
                setUser(fbUser);
                await fetchSubscriptionStatus();
                await loadUserFiles(1, 10);
            } else {
                setUser(null);
                setFiles([]);
            }
            setAuthLoading(false);
        });
        return () => unsubscribe();
    }, [registerUserInBackend, fetchSubscriptionStatus, loadUserFiles, disconnectWebSocket]);

    // WebSocket connection is now managed by the useWebSocket hook

    const contextValue: UserContextType = {
        user,
        setUser,
        darkMode,
        toggleDarkMode,
        setDarkMode,
        userSettings,
        setUserSettings,
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
        webSocketStatus,
        files,
        setFiles,
        filePagination,
        setFilePagination,
        isLoadingFiles,
        setIsLoadingFiles,
        fileError,
        setFileError,
        loadUserFiles,
        deleteFileFromContext,
        authLoading,
    };

    return (
        <UserContext.Provider value={contextValue}>
            {children}
        </UserContext.Provider>
    );
};

export const useUserContext = (): UserContextType => {
    const context = useContext(UserContext);
    if (!context) {
        throw new Error('useUserContext must be used within a UserProvider');
    }
    return context;
};