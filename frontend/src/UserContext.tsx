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

export interface IncomingChatMessage {
    historyId: number | null;
    content: string;
    isError: boolean;
    receivedAt: number;
}

export interface OrgContext {
    organization_id: number;
    name: string | null;
    role: string;
    subscription_status: string | null;
    entitled: boolean;
    can_manage_billing: boolean;
    can_manage_members: boolean;
    can_manage_documents: boolean;
    can_rename_organization: boolean;
    can_create_workspace: boolean;
    capabilities: string[];
    seats_used: number;
    seat_limit: number | null;
}

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
    // True when premium access applies, whether the user pays personally or
    // inherits it as staff of a paid workspace. Route on this, not on
    // subscriptionStatus, which is only ever about this user's own billing.
    isEntitled: boolean;
    // Owns at least one workspace, so billing and org administration are theirs.
    isOrgOwner: boolean;
    // Belongs to workspaces but owns none: a pure invitee. Never show billing,
    // and never ask them to fix somebody else's lapsed plan.
    isMemberOnly: boolean;
    // Role in the workspace currently selected: 'owner' | 'staff' | null.
    // Permissions are per workspace, so the UI must key off this rather than
    // any account-level flag.
    currentWorkspaceRole: string | null;
    setCurrentWorkspaceRole: (role: string | null) => void;
    // Answers arrive over the websocket, but the conversation lives in ChatApp,
    // so the socket handler parks the latest one here for it to consume.
    incomingChatMessage: IncomingChatMessage | null;
    // Timestamp of the last access change pushed from the server.
    accessChangedAt: number;
    clearIncomingChatMessage: () => void;
    // The tenant the user chose at sign-in. Everything is scoped to it, so
    // entitlement and role can never be blended across two organizations the
    // user happens to belong to.
    activeOrganizationId: number | null;
    orgContext: OrgContext | null;
    setActiveOrganization: (organizationId: number) => Promise<void>;
    clearActiveOrganization: () => void;
    fetchSubscriptionStatus: () => void;
    subscriptionData: SubscriptionData | null;
    setSubscriptionData: (data: SubscriptionData | null) => void;
    registerUserInBackend: (user: FirebaseUser, intent?: 'signin' | 'signup') => Promise<void>;
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
    /**
     * workspaceId is required, and null means "the whole organization" as a
     * deliberate choice rather than an oversight. It was optional, and the two
     * callers that forgot it reloaded the list unscoped after an upload: the
     * file went into the right workspace and the panel then showed every
     * document in the company.
     */
    loadUserFiles: (page: number, pageSize: number, workspaceId: number | null) => Promise<void>;
    // workspaceId is required, not optional. It defaulted to null, and a
    // default is what let this go wrong twice: the refresh fell through to the
    // organization filter and a workspace showing 3 documents redrew with 12.
    // A caller that forgets should fail to compile rather than silently show
    // somebody every document in the company.
    deleteFileFromContext: (fileId: number, workspaceId: number | null) => Promise<void>;
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
    const [isEntitled, setIsEntitled] = useState<boolean>(false);
    const [isOrgOwner, setIsOrgOwner] = useState<boolean>(false);
    const [isMemberOnly, setIsMemberOnly] = useState<boolean>(false);
    const [currentWorkspaceRole, setCurrentWorkspaceRole] = useState<string | null>(null);
    const [incomingChatMessage, setIncomingChatMessage] = useState<IncomingChatMessage | null>(null);
    // Bumped whenever access changes, so views holding workspace-scoped lists
    // can refetch without each of them subscribing to the socket.
    const [accessChangedAt, setAccessChangedAt] = useState<number>(0);
    const clearIncomingChatMessage = useCallback(() => setIncomingChatMessage(null), []);
    const [activeOrganizationId, setActiveOrganizationId] = useState<number | null>(() => {
        const stored = localStorage.getItem('active_organization_id');
        return stored ? Number(stored) : null;
    });
    const [orgContext, setOrgContext] = useState<OrgContext | null>(null);
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

    // Who is signed in, readable synchronously.
    //
    // The `user` state is a render behind during the one moment it matters
    // most. Firebase reports an account, the listener calls setUser and then
    // immediately calls the handlers that need it — but those closures were
    // built on the previous render, where user was still null, so they returned
    // at their own guard and did nothing. Sign-in fetched no subscription
    // status, which left it null, which is the condition Auth waits on before
    // redirecting: signed in, parked on the sign-in page, with only a sign out
    // button on it.
    //
    // This used to be papered over by the auth listener re-subscribing whenever
    // a handler changed identity, which fired it again with fresh closures.
    // That churn was the organization loop, so removing it exposed this. A ref
    // updated before the handlers run is the same guarantee without the loop.
    const userRef = useRef<FirebaseUser | null>(null);
    const currentUser = () => user ?? userRef.current;

    const toggleDarkMode = () => setDarkMode((prev) => !prev);

    const _callApiWithTokenInternal = useCallback(async (url: string, method: string, body?: any) => {
        const user = currentUser();
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

    const loadUserFiles = useCallback(async (page: number, pageSize: number, workspaceId: number | null) => {
        setIsLoadingFiles(true);
        setFileError(null);
        let url = `/api/v1/files?page=${page}&page_size=${pageSize}`;
        if (workspaceId !== null) {
            url += `&workspace_id=${workspaceId}`;
        } else if (activeOrganizationId) {
            // Without a workspace filter, keep results inside the organization
            // the user chose, so somebody in two companies never gets both
            // companies' documents in one list.
            url += `&organization_id=${activeOrganizationId}`;
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
    }, [_callApiWithTokenInternal, activeOrganizationId]);

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

                    // The backend has been sending this since chat was built, and
                    // nothing listened for it: the switch handled only file
                    // events, so every answer was delivered and dropped. The
                    // request succeeded, the worker succeeded, the socket
                    // delivered, and the conversation stayed empty.
                    // Access was changed by an owner while this tab was open.
                    //
                    // The workspace, document and conversation lists were all
                    // fetched at sign-in and never revisited, so somebody who
                    // lost a workspace kept seeing it until they happened to
                    // reload — which for a revocation is exactly the wrong way
                    // round. Re-resolving the organization refreshes what they
                    // may do, and the workspace picker reloads from it.
                    case 'access_changed': {
                        const orgId = (parsedMessage.data as any)?.organization_id;
                        if (orgId != null) {
                            setActiveOrganization(orgId).catch(() => {});
                        }
                        setAccessChangedAt(Date.now());
                        break;
                    }

                    // Removed from a company outright.
                    //
                    // Say which one, then let go of it. Everything on screen was
                    // fetched when they signed in and is now refused, so leaving
                    // it there looks like access survived removal.
                    //
                    // Deliberately not a sign-out. Losing one company is not
                    // losing an account: they may own another or belong to
                    // several, and clearing the tenant sends them wherever they
                    // still belong. Somebody with nothing left lands on sign-up,
                    // still signed in.
                    case 'removed_from_organization': {
                        const data: any = parsedMessage.data || {};
                        const name = data.organization_name || 'that organization';
                        addToast(
                            `You no longer have access to ${name}. If that's a mistake, ask whoever manages it.`,
                            'warning',
                        );
                        clearActiveOrganization();
                        setAccessChangedAt(Date.now());
                        break;
                    }

                    case 'message_received': {
                        const data: any = parsedMessage.data || {};
                        setIncomingChatMessage({
                            historyId: data.history_id ?? null,
                            content: data.status === 'error'
                                ? (data.error || 'Something went wrong generating that answer.')
                                : (data.message || ''),
                            isError: data.status === 'error',
                            receivedAt: Date.now(),
                        });
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

    const clearActiveOrganization = useCallback(() => {
        localStorage.removeItem('active_organization_id');
        setActiveOrganizationId(null);
        setOrgContext(null);
    }, []);

    /**
     * Enter an organization. Loads what the user may do inside that one tenant,
     * so role, entitlement and billing visibility are answered per organization
     * rather than blended across every organization they belong to.
     */
    const setActiveOrganization = useCallback(async (organizationId: number) => {
        const user = currentUser();
        if (!user) return;
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/organizations/${organizationId}/context`, {
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (!res.ok) {
                // Membership may have been revoked while they were away.
                console.warn('Could not enter organization', organizationId);
                clearActiveOrganization();
                return;
            }
            const ctx: OrgContext = await res.json();
            localStorage.setItem('active_organization_id', String(organizationId));
            setActiveOrganizationId(organizationId);
            setOrgContext(ctx);
        } catch (e) {
            console.error('Error entering organization', e);
            clearActiveOrganization();
        }
    }, [user, clearActiveOrganization]);

    // Re-resolve the stored organization on load, so a refresh keeps the user
    // in the same tenant without asking again.
    useEffect(() => {
        if (user && activeOrganizationId !== null && orgContext === null) {
            setActiveOrganization(activeOrganizationId);
        }
    }, [user, activeOrganizationId, orgContext, setActiveOrganization]);

    const fetchSubscriptionStatus = useCallback(async () => {
        if (!currentUser()) return;
        // Scoped to the organization being viewed. Without it the answer came
        // back for the person, so somebody inside their own unpaid company saw
        // the subscription of a different company they happen to belong to.
        const orgId = localStorage.getItem('active_organization_id');
        const url = orgId
            ? `/api/v1/subscriptions/status?organization_id=${encodeURIComponent(orgId)}`
            : '/api/v1/subscriptions/status';
        const response = await _callApiWithTokenInternal(url, 'GET');
        if (response?.ok) {
            const data = await response.json();
            setSubscriptionStatus(data.subscription_status ?? 'none');
            // Fall back to the personal status if an older backend omits the
            // field, so this cannot regress into locking everyone out.
            setIsEntitled(
                data.entitled ?? ['active', 'trialing'].includes(data.subscription_status)
            );
            // Default to owner when an older backend omits these, so nobody is
            // locked out of their own billing by a missing field.
            setIsOrgOwner(data.is_org_owner ?? true);
            setIsMemberOnly(data.is_member_only ?? false);
            setSubscriptionData(data);

            // Entitlement is a property of the organization, so a subscription
            // change makes the cached organization context stale. Nothing
            // refreshed it, which meant that after starting a trial the app
            // still believed the organization was unentitled: chat stayed
            // locked and the settings close button stayed hidden until some
            // unrelated action happened to re-resolve it, such as renaming the
            // organization. Refresh it here so every caller is covered rather
            // than each one remembering.
            //
            // Refresh the organization this status was actually fetched for,
            // read at the top of this call. Using the activeOrganizationId
            // captured when this callback was built put the app back into the
            // *previous* organization: switching sets the state and this
            // closure still holds the old value, so signing up bounced the
            // person straight out of the company they had just created. With
            // one organization the wrong id was the right id and it never
            // showed.
            if (orgId) {
                await setActiveOrganization(Number(orgId));
            }
        } else {
            // Set to 'none' so the Auth.tsx redirect condition (subscriptionStatus !== null)
            // fires instead of leaving the user stuck on the auth page indefinitely.
            console.error('Failed to fetch subscription status');
            setSubscriptionStatus('none');
            setIsEntitled(false);
            setIsOrgOwner(true);
            setIsMemberOnly(false);
        }
        // activeOrganizationId is deliberately absent: this reads the id from
        // storage, so depending on the state only churned this callback's
        // identity every time the organization changed.
    }, [user, _callApiWithTokenInternal, setActiveOrganization]);

    const registerUserInBackend = useCallback(async (
        fbUser: FirebaseUser,
        intent: 'signin' | 'signup' = 'signin',
    ) => {
        // The intent is the whole point of having two buttons. It used to be a
        // label only, so the backend keyed off whether a user row existed and
        // an invited member could never start a company of their own.
        const idToken = await fbUser.getIdToken();
        const response = await fetch(`/api/v1/users?intent=${intent}`, {
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

    const deleteFileFromContext = useCallback(async (fileId: number, workspaceId: number | null) => {
        const url = `/api/v1/files/${fileId}`;
        const response = await _callApiWithTokenInternal(url, 'DELETE');
        if (response?.ok) {
            addToast('File deleted successfully!', 'success');
            // Reload in the scope the caller was standing in. This passed null,
            // which falls through to the organization filter, so deleting one
            // document from a workspace redrew the list with every document in
            // the company. Same defect as the upload refresh, and the comment
            // here argued it was correct: "no workspace is selected during a
            // refresh triggered from here". A workspace is selected. The
            // context does not hold it, so the caller has to say.
            await loadUserFiles(filePagination.page, filePagination.pageSize, workspaceId);
        } else {
            addToast('Failed to delete file.', 'error');
        }
    }, [_callApiWithTokenInternal, addToast, loadUserFiles, filePagination.page, filePagination.pageSize]);
    
    // Subscribe to Firebase once, for the life of the provider.
    //
    // The dependency list used to name these four callbacks, two of which
    // (fetchSubscriptionStatus, loadUserFiles) are rebuilt whenever the active
    // organization changes. So every organization change tore down the auth
    // listener and re-subscribed it, and re-subscribing fires the callback
    // immediately with the current user — re-registering the account against
    // the backend and re-fetching everything. Paired with a stale id putting
    // the app back in the previous organization, that is a loop: it flipped
    // between the two companies for as long as the tab was in front,
    // registering the user hundreds of times, and paused when the tab was
    // backgrounded because browsers throttle it there.
    //
    // A ref keeps the handlers current without the subscription depending on
    // their identity.
    const authHandlers = useRef({
        registerUserInBackend,
        fetchSubscriptionStatus,
        loadUserFiles,
        disconnectWebSocket,
    });
    useEffect(() => {
        authHandlers.current = {
            registerUserInBackend,
            fetchSubscriptionStatus,
            loadUserFiles,
            disconnectWebSocket,
        };
    }, [registerUserInBackend, fetchSubscriptionStatus, loadUserFiles, disconnectWebSocket]);

    useEffect(() => {
        const auth = getAuth();
        const unsubscribe = onAuthStateChanged(auth, async (fbUser) => {
            const {
                registerUserInBackend,
                fetchSubscriptionStatus,
                loadUserFiles,
                disconnectWebSocket,
            } = authHandlers.current;
            // Before anything else, and before setUser, because the handlers
            // below run in this same tick and read it.
            userRef.current = fbUser;
            setAuthLoading(true);
            if (fbUser) {
                // The button that started this is long gone by the time
                // Firebase reports back, so the intent is parked before the
                // popup opens and read here. Defaults to signin, which never
                // creates anything.
                const intent = (sessionStorage.getItem('auth_intent') === 'signup')
                    ? 'signup' : 'signin';
                sessionStorage.removeItem('auth_intent');
                await registerUserInBackend(fbUser, intent);
                setUser(fbUser);
                await fetchSubscriptionStatus();
                // Runs before any workspace has been chosen.
                await loadUserFiles(1, 10, null);
            } else {
                setUser(null);
                setFiles([]);
                disconnectWebSocket();
            }
            setAuthLoading(false);
        });
        return () => unsubscribe();
        // Empty on purpose. See the note above the ref.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Entering an organization asks again what that organization is entitled
    // to.
    //
    // Entitlement is held by the organization, never by the person, so a status
    // fetched before one was chosen answers for nobody: it comes back "none"
    // and the app shows a paying company the free-plan banner and the upgrade
    // prompt. This used to happen by accident — the callback's identity changed
    // whenever the organization did, and an effect elsewhere re-ran because of
    // it. Removing that churn is what stopped the app looping, and it took this
    // refresh with it. Doing it deliberately is the same work without the loop:
    // the handler is reached through the ref, so this depends on the id alone.
    useEffect(() => {
        if (!user || activeOrganizationId == null) return;
        authHandlers.current.fetchSubscriptionStatus();
    }, [user, activeOrganizationId]);

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
        isEntitled,
        isOrgOwner,
        isMemberOnly,
        currentWorkspaceRole,
        setCurrentWorkspaceRole,
        incomingChatMessage,
        clearIncomingChatMessage,
        accessChangedAt,
        activeOrganizationId,
        orgContext,
        setActiveOrganization,
        clearActiveOrganization,
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