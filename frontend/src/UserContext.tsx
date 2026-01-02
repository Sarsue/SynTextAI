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
    card_last4?: string | null;
    card_brand?: string | null;
    card_exp_month?: number | null;
    card_exp_year?: number | null;
    trial_end?: string | null;
    current_period_end?: string | null;
    has_active_payment_method?: boolean;
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
    loadUserFiles: (page: number, pageSize: number, workspaceId?: number | null) => Promise<void>;
    deleteFileFromContext: (fileId: number) => Promise<void>;
    pollFileStatus: () => Promise<void>; // Trigger immediate status check
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
    const filesRef = useRef<UploadedFile[]>([]);

    const toggleDarkMode = () => setDarkMode((prev) => !prev);

    const _callApiWithTokenInternal = useCallback(async (url: string, method: string, body?: any) => {
        if (!user) return null;
        try {
            const buildHeaders = (token: string): HeadersInit => {
                const headers: HeadersInit = { 'Authorization': `Bearer ${token}` };
                if (body && !(body instanceof FormData)) {
                    headers['Content-Type'] = 'application/json';
                }
                return headers;
            };

            const idToken = await user.getIdToken();
            let response = await fetch(url, {
                method,
                headers: buildHeaders(idToken),
                body: (body && body instanceof FormData) ? body : (body ? JSON.stringify(body) : undefined)
            });

            if (response.status === 401) {
                const refreshed = await user.getIdToken(true);
                response = await fetch(url, {
                    method,
                    headers: buildHeaders(refreshed),
                    body: (body && body instanceof FormData) ? body : (body ? JSON.stringify(body) : undefined)
                });
            }
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

    const loadUserFiles = useCallback(async (page: number, pageSize: number, workspaceId?: number | null) => {
        setIsLoadingFiles(true);
        setFileError(null);
        let url = `/api/v1/files?page=${page}&page_size=${pageSize}`;
        if (workspaceId !== null && workspaceId !== undefined) {
            url += `&workspace_id=${workspaceId}`;
        }
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

    // keep files ref in sync to avoid effect retriggers
    useEffect(() => { filesRef.current = files; }, [files]);

    // Exposed function to trigger immediate status polling (e.g., after file upload)
    const pollFileStatus = useCallback(async () => {
        if (!user) return;
        const isTerminal = (status: string | undefined) => status === 'processed' || status === 'failed';
        
        try {
            const currentFiles = filesRef.current;
            if (!currentFiles || currentFiles.length === 0) return;
            const pending = currentFiles.filter(f => !isTerminal(f.status));
            if (pending.length === 0) return;

            const ids = pending.map(f => f.id).join(',');
            const url = `/api/v1/files/status?ids=${ids}`;
            const response = await _callApiWithTokenInternal(url, 'GET');
            if (!response?.ok) return;
            const data = await response.json();
            const items: Array<{ file_id: number; processing_status: string; progress: number }> = data.items || [];
            if (items.length === 0) return;

            setFiles(prev => prev.map(file => {
                const found = items.find(it => it.file_id === file.id);
                if (!found) return file;
                return { ...file, status: found.processing_status as any };
            }));
        } catch (e) {
            console.warn('Status polling error', e);
        }
    }, [user, _callApiWithTokenInternal, setFiles]);

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
                const parsedMessage: KnownWebSocketMessage = JSON.parse(event.data);

                switch (parsedMessage.event) {
                    case 'file_processed': {
                        const updatedFile = (parsedMessage.result || parsedMessage.data) as UploadedFile;
                        setFiles(prevFiles => prevFiles.map(f => (f.id === updatedFile.id ? updatedFile : f)));
                        addToast(`File "${updatedFile.file_name}" has been processed.`, 'success');
                        break;
                    }

                    case 'file_status_update': {
                        const data = parsedMessage.data as FileStatusUpdatePayload;
                        if (data?.file_id && data?.status) {
                            setFiles(prevFiles =>
                                prevFiles.map(f => (f.id === data.file_id ? { ...f, status: data.status } : f))
                            );
                            if (data.status === 'processed') {
                                addToast('File processing completed.', 'success');
                            }
                        }
                        break;
                    }

                    case 'file_status_error': {
                        break;
                    }

                    case 'file_deleted': {
                        const data = parsedMessage.data as { file_id: number };
                        setFiles(prevFiles => prevFiles.filter(f => f.id !== data.file_id));
                        addToast(`File was deleted.`, 'info');
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
        pollFileStatus,
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