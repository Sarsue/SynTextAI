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
    const [socket, setSocket] = useState<WebSocket | null>(null);
    const [webSocketStatus, setWebSocketStatus] = useState<WebSocketStatus>('disconnected');
    
    // File state
    const [files, setFiles] = useState<UploadedFile[]>([]);
    const [filePagination, setFilePagination] = useState<PaginationState>({ page: 1, pageSize: 10, totalItems: 0 });
    const [isLoadingFiles, setIsLoadingFiles] = useState<boolean>(false);
    const [fileError, setFileError] = useState<string | null>(null);

    const socketRef = useRef<WebSocket | null>(null);
    const reconnectionAttempts = useRef(0);
    const reconnectTimeoutId = useRef<NodeJS.Timeout | null>(null);

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
    
    const disconnectWebSocket = useCallback(() => {
        setWebSocketStatus('disconnected');
        if (reconnectTimeoutId.current) {
            clearTimeout(reconnectTimeoutId.current);
            reconnectTimeoutId.current = null;
        }
        if (socketRef.current) {
            socketRef.current.onclose = null; 
            socketRef.current.close();
            socketRef.current = null;
            setSocket(null);
        }
    }, []);

    const initializeWebSocket = useCallback(async () => {
        setWebSocketStatus('connecting');
        if (!user || socketRef.current) return;

        const token = await user.getIdToken();
        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const wsUrl = `${protocol}://${window.location.host}/ws/${user.uid}`;
        
        const ws = new WebSocket(wsUrl);
        socketRef.current = ws;
        setSocket(ws);

        ws.onopen = () => {
            setWebSocketStatus('connected');
            console.log('WebSocket connection established.');
            addToast('Real-time connection established.', 'success');
            reconnectionAttempts.current = 0;
            if (reconnectTimeoutId.current) clearTimeout(reconnectTimeoutId.current);
            ws.send(JSON.stringify({ type: 'auth', token }));
        };

        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data) as KnownWebSocketMessage;
                switch (message.event) {
                    case 'file_processed': {
                        const updatedFile = message.result as UploadedFile;
                        setFiles(prev => prev.map(f => (f.id === updatedFile.id ? updatedFile : f)));
                        addToast(`File "${updatedFile.name}" processed.`, 'success');
                        break;
                    }
                    case 'file_status_update':
                    case 'file_status_error': {
                        const data = message.data as FileStatusUpdatePayload;
                        setFiles(prev => prev.map(f => f.id === data.file_id ? { ...f, processing_status: data.status, error_message: data.error ?? f.error_message } : f));
                        if (data.status === 'failed') addToast(`Error processing file: ${data.error}`, 'error');
                        break;
                    }
                    case 'file_deleted': {
                        const data = message.data as { file_id: number };
                        setFiles(prev => prev.filter(f => f.id !== data.file_id));
                        addToast(`File deleted.`, 'info');
                        break;
                    }
                }
            } catch (error) {
                console.error("Error processing WebSocket message:", error);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            addToast('WebSocket connection error.', 'error');
        };

        ws.onclose = () => {
            if (!socketRef.current) return;
            
            socketRef.current = null;
            setSocket(null);

            if (reconnectionAttempts.current < MAX_RECONNECT_ATTEMPTS) {
                setWebSocketStatus('reconnecting');
                reconnectionAttempts.current++;
                const delay = Math.pow(2, reconnectionAttempts.current) * 1000;
                addToast(`Connection lost. Reconnecting...`, 'warning');
                reconnectTimeoutId.current = setTimeout(initializeWebSocket, delay);
            } else {
                setWebSocketStatus('disconnected');
                addToast('Could not re-establish real-time connection. Please refresh the page.', 'error');
            }
        };
    }, [user, addToast]);

    const fetchSubscriptionStatus = useCallback(async () => {
        if (!user) return;
        const response = await _callApiWithTokenInternal('/api/v1/subscriptions/status', 'GET');
        if (response?.ok) {
            const data = await response.json();
            setSubscriptionStatus(data.subscription_status ?? null);
            setSubscriptionData(data);
        } else {
            console.error('Failed to fetch subscription status');
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
                disconnectWebSocket();
            }
            setAuthLoading(false);
        });
        return () => unsubscribe();
    }, [registerUserInBackend, fetchSubscriptionStatus, loadUserFiles, disconnectWebSocket]);

    useEffect(() => {
        if (user) {
            initializeWebSocket();
        } else {
            disconnectWebSocket();
        }
        return () => disconnectWebSocket();
    }, [user, initializeWebSocket, disconnectWebSocket]);

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