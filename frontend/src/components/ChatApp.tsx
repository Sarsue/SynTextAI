
import React, { useEffect, useState, useCallback, useRef } from 'react';

import { useNavigate, useLocation } from 'react-router-dom';
import ConversationView from './ConversationView';
import InputArea, { ComposerMode } from './InputArea';
import SearchResults, { SearchHit } from './SearchResults';
import HistoryView from './HistoryView';
import { Message, History, MessageFeedback } from '../components/types';
import './ChatApp.css';
import { User } from 'firebase/auth';
import { useUserContext , ALL_WORKSPACES } from '../UserContext';
import { useToast } from '../contexts/ToastContext';
import KnowledgeBaseComponent from './KnowledgeBaseComponent';
import FileViewerComponent from './FileViewerComponent';
import { Persona, UploadedFile } from './types';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import useAnalytics from '../hooks/useAnalytics';
import { AnalyticsEvents, createEventProperties } from '../utils/analyticsEvents';
import { trackPageView, trackAction, trackError, getPosthog } from '../utils/analyticsQueue';
import { Navigate } from 'react-router-dom';
import { WebSocketMessage, FileStatusUpdatePayload } from '../types/websocketTypes';
import WebSocketStatusIndicator from './WebSocketStatusIndicator';


interface ChatAppProps {
    user: User | null;
    onLogout: () => void;
}

const ChatApp: React.FC<ChatAppProps> = ({ user: initialUser, onLogout }) => {
    const {
        user,
        setUser,
        darkMode,
        toggleDarkMode,
        setDarkMode: setContextDarkMode,

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
        files: userFiles, 
        setFiles: setContextFiles, 
        filePagination, 
        setFilePagination: setContextFilePagination, 
        isLoadingFiles,
        setIsLoadingFiles: setContextIsLoadingFiles, 
        fileError,
        setFileError: setContextFileError, 
        loadUserFiles, 
        deleteFileFromContext, // Added for centralized deletion
        pollFileStatus, // Trigger immediate status check after upload
        authLoading,
        incomingChatMessage,
        clearIncomingChatMessage,
        activeOrganizationId,
        orgContext,
        accessChangedAt
    } = useUserContext();
    const { addToast } = useToast();
    const { webSocketStatus } = useUserContext();
    const { capture, identify } = useAnalytics();
    const location = useLocation();
    const prevPathRef = useRef('');
    
    // Track page views on route change
    useEffect(() => {
        if (location.pathname !== prevPathRef.current) {
            trackPageView(location.pathname);
            prevPathRef.current = location.pathname;
        }
    }, [location]);

    // Identify user when user is available
    useEffect(() => {
        if (user?.uid) {
            identify(user.uid, {
                email: user.email,
                email_verified: user.emailVerified,
                created_at: user.metadata.creationTime,
                last_login: user.metadata.lastSignInTime,
            });
        }
    }, [user, identify]);

    const [histories, setHistories] = useState<{ [key: number]: History }>({});
    const [currentHistory, setCurrentHistory] = useState<number | null>(null);

    const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null);
    const [currentWorkspaceId, setCurrentWorkspaceId] = useState<number | null>(null);
    const idTokenRef = useRef<string | null>(null); 
    const navigate = useNavigate();
    // Only offer the organization switcher to people who actually have a
    // choice, which is a small minority.
    const [hasMultipleOrganizations, setHasMultipleOrganizations] = useState(false);
    const [activeTab, setActiveTab] = useState("chat"); 
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
    const [isSending, setIsSending] = useState(false);
    // Ask or Find. Two different products sharing one box: an answer, or the
    // page it is on. Held here rather than in InputArea because the results
    // replace the conversation, so the main panel has to know too.
    const [composerMode, setComposerMode] = useState<ComposerMode>('ask');
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<SearchHit[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const [hasSearched, setHasSearched] = useState(false);
    // isSending only covers the POST, which returns as soon as the question is
    // queued. The answer arrives later over the websocket, so between those two
    // moments the UI had nothing to say and the app looked like it had simply
    // eaten the question. This tracks the wait itself, per conversation, so the
    // indicator appears in the thread the answer belongs to.
    const [awaitingReplyFor, setAwaitingReplyFor] = useState<number | null>(null);
    const awaitingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // A websocket that drops leaves the browser waiting on an event that will
    // never come, which is how "Sending..." got stuck forever. Give the wait a
    // deadline and say so, rather than spinning indefinitely.
    const startAwaitingReply = useCallback((historyId: number) => {
        if (awaitingTimeoutRef.current) clearTimeout(awaitingTimeoutRef.current);
        setAwaitingReplyFor(historyId);
        awaitingTimeoutRef.current = setTimeout(() => {
            setAwaitingReplyFor(null);
            addToast('Still working on that answer. Refresh to see it once it lands.', 'error');
        }, 180000);
    }, [addToast]);

    const stopAwaitingReply = useCallback(() => {
        if (awaitingTimeoutRef.current) {
            clearTimeout(awaitingTimeoutRef.current);
            awaitingTimeoutRef.current = null;
        }
        setAwaitingReplyFor(null);
    }, []);

    useEffect(() => () => {
        if (awaitingTimeoutRef.current) clearTimeout(awaitingTimeoutRef.current);
    }, []);

    // Placeholder chat handlers - implement actual logic as needed
    const handleSendMessage = async (messageContent: string, historyId: number | null, attachments?: File[]) => {
        console.log('handleSendMessage called with:', { messageContent, historyId, attachments });
        trackAction('send_message', 'chat', historyId?.toString());
        setIsSending(true);
        setTimeout(() => {
            const newMessage: Message = {
                id: Date.now(), 
                sender: 'user',
                content: messageContent,
                timestamp: new Date().toISOString(),
                feedback: null,
            };
            const targetHistoryId = historyId || currentHistory || Date.now(); 
            setHistories(prev => ({
                ...prev,
                [targetHistoryId]: {
                    ...(prev[targetHistoryId] || { id: targetHistoryId, title: 'New Chat', messages: [] }),
                    messages: [...(prev[targetHistoryId]?.messages || []), newMessage]
                }
            }));
            setIsSending(false); 
        }, 1000);
        return true; 
    };

    // Render answers pushed over the websocket.
    //
    // The worker generates the answer, saves it, and notifies the API, which
    // relays it to the browser. Nothing consumed that event, so answers arrived
    // and were dropped: the conversation stayed empty even though the reply was
    // already in the database. This is the missing last hop.
    useEffect(() => {
        if (!incomingChatMessage) return;

        const targetHistoryId = incomingChatMessage.historyId ?? currentHistory;
        if (targetHistoryId == null) {
            clearIncomingChatMessage();
            return;
        }

        const botMessage: Message = {
            id: Date.now(),
            sender: 'bot',
            content: incomingChatMessage.content,
            timestamp: new Date().toISOString(),
            feedback: null,
        };

        setHistories(prev => ({
            ...prev,
            [targetHistoryId]: {
                ...(prev[targetHistoryId] || { id: targetHistoryId, title: 'New Chat', messages: [] }),
                messages: [...(prev[targetHistoryId]?.messages || []), botMessage],
            },
        }));
        setIsSending(false);
        stopAwaitingReply();
        clearIncomingChatMessage();
    }, [incomingChatMessage, currentHistory, clearIncomingChatMessage, stopAwaitingReply]);

    // Load the conversation list.
    //
    // fetchHistories was defined and never called: no effect, no invocation
    // anywhere. GET /histories was therefore never requested, and the History
    // tab was permanently empty no matter how many conversations existed.
    // Refetches on workspace change too: conversations belong to a workspace,
    // so switching workspace has to switch the thread list the same way it
    // switches the document list, or the sidebar offers threads whose citations
    // point at files this workspace cannot open.
    useEffect(() => {
        if (!user) return;
        fetchHistories();
        // The open conversation belongs to the workspace being left.
        setCurrentHistory(null);
        stopAwaitingReply();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [user, currentWorkspaceId, accessChangedAt]);

    // Load a conversation's messages when it is opened.
    //
    // The list endpoint returns a preview, not the messages, and the endpoint
    // that does return them was never called either, so selecting a past
    // conversation showed an empty thread.
    useEffect(() => {
        if (!user || currentHistory == null) return;
        if ((histories[currentHistory]?.messages?.length ?? 0) > 0) return;

        let cancelled = false;
        (async () => {
            try {
                const res = await callApiWithToken(
                    `api/v1/histories/messages?history_id=${encodeURIComponent(currentHistory)}` +
                        (activeOrganizationId != null
                            ? `&organization_id=${encodeURIComponent(activeOrganizationId)}`
                            : ''),
                    'GET'
                );
                if (!res?.ok || cancelled) return;
                const data = await res.json();
                if (!Array.isArray(data) || cancelled) return;
                setHistories(prev => ({
                    ...prev,
                    [currentHistory]: {
                        ...(prev[currentHistory] || { id: currentHistory, title: 'Chat', messages: [] }),
                        messages: data.map((m: any) => ({
                            id: m.id,
                            content: m.content,
                            sender: m.sender,
                            timestamp: m.timestamp,
                            feedback: m.feedback ?? null,
                        })),
                    },
                }));
            } catch (e) {
                console.error('Could not load messages for this conversation', e);
            }
        })();
        return () => { cancelled = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [user, currentHistory]);

    const handleDeleteMessage = async (messageId: number, historyId: number) => {
        console.log('handleDeleteMessage called with:', { messageId, historyId });
        trackAction('delete_message', 'chat', historyId.toString());
        setHistories(prev => {
            const historyToUpdate = prev[historyId];
            if (!historyToUpdate) return prev;
            return {
                ...prev,
                [historyId]: {
                    ...historyToUpdate,
                    messages: historyToUpdate.messages.filter(msg => msg.id !== messageId)
                }
            };
        });
    };

    const handleRegenerateMessage = async (historyId: number) => {
        console.log('handleRegenerateMessage called for historyId:', historyId);
        trackAction('regenerate_message', 'chat', historyId.toString());
        const history = histories[historyId];
        if (history && history.messages.length > 0) {
            const lastUserMessage = [...history.messages].reverse().find(m => m.sender === 'user');
            if (lastUserMessage) {
                setIsSending(true);
                setHistories(prev => {
                    const currentMessages = prev[historyId]?.messages || [];
                    const lastAssistantIndex = currentMessages.map(m => m.sender).lastIndexOf('bot'); 
                    const messagesToKeep = lastAssistantIndex !== -1 && lastAssistantIndex === currentMessages.length -1 
                                           ? currentMessages.slice(0, lastAssistantIndex)
                                           : currentMessages;
                    return {
                        ...prev,
                        [historyId]: {
                            ...prev[historyId],
                            messages: messagesToKeep
                        }
                    };
                });
                await handleSendMessage(lastUserMessage.content, historyId);
            } else {
                addToast('No user message found to regenerate.', 'warning');
            }
        } else {
            addToast('No messages in history to regenerate.', 'warning');
        }
    };

    const SOCKET_RECONNECTION_ATTEMPTS = 5;
    const SOCKET_RECONNECTION_DELAY = 3000;
    const SOCKET_RECONNECTION_DELAY_MAX = 15000;

    useEffect(() => {
        const handleResize = () => {
            setIsMobile(window.innerWidth <= 768);
        };
        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, []);

    useEffect(() => {
        const fetchToken = async () => {
            if (user) {
                try {
                    const token = await user.getIdToken();
                    idTokenRef.current = token;
                } catch (error) {
                    console.error('Error fetching ID token:', error);
                    idTokenRef.current = null;
                }
            } else {
                idTokenRef.current = null;
            }
        };
        fetchToken();
    }, [user]);

    useEffect(() => {
        if (!user) return;
        let cancelled = false;
        (async () => {
            try {
                const idToken = await user.getIdToken();
                const res = await fetch('/api/v1/organizations', {
                    headers: { Authorization: `Bearer ${idToken}` },
                });
                if (!res.ok) return;
                const data = await res.json();
                if (!cancelled) setHasMultipleOrganizations((data.items || []).length > 1);
            } catch {
                // Not being able to tell just means the control stays hidden.
            }
        })();
        return () => { cancelled = true; };
    }, [user]);

    const handleSettingsClick = () => {
        trackAction(AnalyticsEvents.BUTTON_CLICK, 'navigation', 'settings_button');
        navigate('/settings');
    };

    const callApiWithToken = async (apiUrl: string, method: string, body?: any) => {
        if (!user) {
            console.error('User not available for callApiWithToken');
            addToast('Authentication session expired. Please refresh.', 'error');
            return Promise.reject(new Error('User not available'));
        }
        // No guard on idTokenRef here. It is a cache, populated by an effect
        // that runs later in this component and resolves asynchronously, so
        // anything calling this on mount was rejected before a request was ever
        // made. That is what stopped the conversation list from loading: the
        // effect ran, this rejected, and no GET /histories appeared in the
        // network log at all. The next line fetches a token regardless, so
        // requiring the cache to be warm rejected a call it was about to
        // satisfy anyway.
        const tokenForCall = await user.getIdToken();
        idTokenRef.current = tokenForCall;
        let headers: HeadersInit = { 'Authorization': `Bearer ${tokenForCall}` };
        if (body && !(body instanceof FormData)) {
            headers['Content-Type'] = 'application/json';
        }

        try {
            let response = await fetch(apiUrl, {
                method,
                headers,
                mode: 'cors',
                credentials: 'include',
                body: (body && body instanceof FormData) ? body : (body ? JSON.stringify(body) : undefined)
            });
            if (response.status === 401) {
                const refreshed = await user.getIdToken(true);
                idTokenRef.current = refreshed;
                headers = { 'Authorization': `Bearer ${refreshed}` };
                if (body && !(body instanceof FormData)) {
                    headers['Content-Type'] = 'application/json';
                }
                response = await fetch(apiUrl, {
                    method,
                    headers,
                    mode: 'cors',
                    credentials: 'include',
                    body: (body && body instanceof FormData) ? body : (body ? JSON.stringify(body) : undefined)
                });
            }
            return response;
        } catch (error) {
            console.error('Unexpected error calling API:', error);
            addToast('Failed to communicate with the server.', 'error');
            return Promise.reject(error);
        }
    };

    /**
     * Reflect a rating in the conversation the user is looking at.
     *
     * The control posts and reverts on failure; this only owns the displayed
     * state, so the two do not both try to be the source of truth.
     */
    const handleFeedbackChange = (messageId: number, feedback: MessageFeedback | null) => {
        if (currentHistory === null) return;
        setHistories(prev => {
            const history = prev[currentHistory];
            if (!history) return prev;
            return {
                ...prev,
                [currentHistory]: {
                    ...history,
                    messages: history.messages.map(m =>
                        m.id === messageId ? { ...m, feedback } : m
                    ),
                },
            };
        });
        if (feedback) {
            trackAction('answer_feedback', 'message', undefined, feedback.rating);
        }
    };

    const handleCopy = (message: Message) => {
        const textToCopy = message.content;
        trackAction('copy_message', 'message', undefined, textToCopy.length);
        navigator.clipboard.writeText(textToCopy)
            .then(() => { 
                if (import.meta.env.DEV) {
                    console.log('Text successfully copied to clipboard:', textToCopy);
                }
                capture(AnalyticsEvents.BUTTON_CLICK, {
                    action: 'copy_success',
                    content_length: textToCopy.length,
                    message_id: message.id,
                });
            })
            .catch((err) => { 
                console.error('Unable to copy text to clipboard:', err);
                trackError(err, { action: 'copy_message' });
            });
    };

    /**
     * Find, rather than ask.
     *
     * One request, answered directly: no conversation is created, nothing is
     * saved, and no model is called, which is why this returns in well under a
     * second where an answer takes ten. Scoped to the workspace being looked
     * at, the same as everything else on this screen.
     */
    const handleSearch = async (query: string) => {
        const trimmed = query.trim();
        if (!trimmed) return;

        setIsSearching(true);
        setSearchQuery(trimmed);
        setHasSearched(true);
        capture(AnalyticsEvents.CHAT_MESSAGE_SENT, createEventProperties({
            message_length: trimmed.length,
            mode: 'search',
        }));

        try {
            const params = new URLSearchParams({ q: trimmed });
            // Only when a real workspace is selected. currentWorkspaceId is a
            // number here; ALL_WORKSPACES is the sentinel used for the file
            // list, and the backend treats a missing workspace_id as "every
            // workspace this person can reach", which is the same thing.
            if (typeof currentWorkspaceId === 'number') {
                params.set('workspace_id', String(currentWorkspaceId));
            }
            const response = await callApiWithToken(`api/v1/search?${params.toString()}`, 'GET');
            if (!response.ok) {
                // A refusal is the answer here, not a crash: an unpaid
                // organization gets 402 and somebody who has run out of their
                // rate limit gets 429, and both deserve their own words.
                const detail = await response.json().catch(() => null);
                addToast(
                    detail?.detail || 'Could not search right now. Please try again.',
                    'error',
                );
                setSearchResults([]);
                return;
            }
            const data = await response.json();
            setSearchResults(Array.isArray(data?.results) ? data.results : []);
        } catch (error) {
            console.error('Search failed:', error);
            addToast('Could not search right now. Please try again.', 'error');
            setSearchResults([]);
        } finally {
            setIsSearching(false);
        }
    };

    const handleSend = async (message: string, files: File[]) => {
        if (composerMode === 'find') {
            await handleSearch(message);
            return;
        }
        try {
            setIsSending(true);
            
            capture(AnalyticsEvents.CHAT_MESSAGE_SENT, createEventProperties({
                message_length: message.length,
                has_attachments: files.length > 0,
                file_count: files.length,
                file_types: files.map(file => file.type),
            }));

            if (files.length > 0) {
                const formData = new FormData();
                for (let i = 0; i < files.length; i++) {
                    formData.append("files", files[i]);
                }
                try {
                    const startTime = Date.now();
                    // Upload into the workspace being looked at. Without this
                    // the server fell back to the first workspace the uploader
                    // can reach, so switching to another one and adding a
                    // document filed it somewhere else: it never appeared in
                    // the list, which filters by this same id, and it was
                    // stored under the wrong workspace's folder.
                    const uploadWorkspaceParam = currentWorkspaceId != null
                        ? `&workspace_id=${encodeURIComponent(currentWorkspaceId)}`
                        : '';
                    const fileDataResponse = await callApiWithToken(
                        `api/v1/files?language=english${uploadWorkspaceParam}`,
                        'POST',
                        formData
                    );
                    
                    const duration = Date.now() - startTime;
                    
                    if (fileDataResponse && fileDataResponse.ok) {
                        const fileData = await fileDataResponse.json();
                        
                        capture(AnalyticsEvents.FILE_UPLOAD, {
                            success: true,
                            file_count: files.length,
                            file_types: files.map(file => file.type),
                            total_size: files.reduce((acc, file) => acc + file.size, 0),
                            upload_duration_ms: duration,
                        });
                        
                        addToast(fileData.message, 'success'); 
                        
                        // Load files list; status updates will stream via WebSocket
                        // Scoped to the workspace the file was uploaded into.
                        // Reloading without it showed every document in the
                        // organization, including other workspaces'.
                        await loadUserFiles(1, filePagination.pageSize, currentWorkspaceId ?? ALL_WORKSPACES);
                    } else {
                        // Read the body once. A Response can only be consumed
                        // once, so taking .text() here for analytics and then
                        // .json() for the message left the second read throwing
                        // and the reason permanently lost.
                        const rawBody = fileDataResponse
                            ? await fileDataResponse.text().catch(() => '')
                            : '';

                        capture(AnalyticsEvents.FILE_UPLOAD, {
                            success: false,
                            file_count: files.length,
                            error: rawBody || 'No response',
                            status: fileDataResponse?.status,
                        });

                        // Say why. A 403 here means they are not allowed to
                        // upload, which 'please try again' invites them to do
                        // forever.
                        let detail: unknown = null;
                        try {
                            detail = JSON.parse(rawBody)?.detail;
                        } catch {
                            // Not JSON. The generic message below covers it.
                        }
                        addToast(
                            typeof detail === 'string'
                                ? detail
                                : 'File upload failed. Please try again.',
                            'error',
                        );
                    }
                } catch (error) {
                    // Track file upload error
                    trackError(error as Error, {
                        action: 'file_upload',
                        file_count: files.length,
                    });
                    
                    addToast('An error occurred during file upload. Please try again.', 'error'); 
                }
                if (!message.trim()) {
                    setIsSending(false); // Re-enable input if only uploading files
                    return;
                }
            }
            // Track message sending
            const messageStartTime = Date.now();
            try {
                await sendMessage(message);
                
                // Track successful message send
                capture(AnalyticsEvents.CHAT_MESSAGE_SENT, {
                    success: true,
                    message_length: message.length,
                    history_id: currentHistory,
                    response_time_ms: Date.now() - messageStartTime,
                });
            } catch (error) {
                // Track message send error
                capture(AnalyticsEvents.CHAT_MESSAGE_SENT, {
                    success: false,
                    error: (error as Error).message,
                    message_length: message.length,
                    history_id: currentHistory,
                });
                throw error; // Re-throw to be caught by the outer try-catch
            }
        } catch (error) {
            console.error('Error sending message:', error);
        } finally {
            // Always clear the sending state. It used to be cleared only on the
            // success and error paths, so a branch that silently did nothing,
            // such as a response missing the field the code guarded on, left the
            // composer stuck on "Sending..." with no error to show for it.
            setIsSending(false);
        }
    };

    const sendMessage = async (message: string) => {
        // One path, no silent dead ends.
        //
        // This used to branch on currentHistory, look the conversation up in
        // state, and only send if it was found, with no else. When an id was set
        // but its entry was missing from state, sending did nothing at all: no
        // request, no error, no state change, which is indistinguishable from
        // the feature being broken. The id is all that is needed to send, so the
        // lookup no longer gates anything.
        let historyId: number | null =
            currentHistory !== null && !isNaN(currentHistory) ? currentHistory : null;

        if (historyId === null) {
            // File the new conversation in the workspace it is being answered
            // from, so it appears here and only here.
            const workspaceParam = currentWorkspaceId != null
                ? `&workspace_id=${encodeURIComponent(currentWorkspaceId)}`
                : '';
            const createHistoryResponse = await callApiWithToken(
                `api/v1/histories?title=${encodeURIComponent(message || 'New Chat')}${workspaceParam}`,
                'POST'
            );
            const createHistoryData = createHistoryResponse?.ok
                ? await createHistoryResponse.json().catch(() => null)
                : null;
            if (!createHistoryData?.id) {
                addToast('Could not start a new conversation.', 'error');
                return;
            }
            historyId = createHistoryData.id as number;
            const created = historyId as number;
            setHistories(prev => ({
                ...prev,
                [created]: { id: created, title: createHistoryData.title || message, messages: [] },
            }));
            setCurrentHistory(created);
        }

        const target = historyId as number;
        // The question goes in the body, never the URL. As a query parameter it
        // landed in the access log in full, and a URL travels further than a log
        // does: the reverse proxy, the CDN, the browser's own history, and the
        // Referer header sent to any third party the page talks to afterwards.
        // For a dental or legal practice the question is usually the most
        // sensitive string in the whole request.
        const response = await callApiWithToken('api/v1/messages', 'POST', {
            message,
            history_id: target,
            language: 'english',
            ...(currentWorkspaceId != null ? { workspace_id: currentWorkspaceId } : {}),
        });

        if (!response?.ok) {
            addToast('Could not send your message.', 'error');
            return;
        }

        const payload = await response.json().catch(() => null);
        const returned: Message[] = Array.isArray(payload)
            ? payload
                // Tolerate entries that are not message objects instead of
                // throwing on them, which is how a response of bare ids used to
                // abort the entire send.
                .filter((m: any) => m && typeof m === 'object' && m.content != null)
                .map((m: any) => ({
                    id: m.id ?? Date.now(),
                    content: m.content,
                    sender: m.sender === 'bot' ? 'bot' : 'user',
                    timestamp: m.timestamp ? new Date(m.timestamp).toISOString() : new Date().toISOString(),
                    feedback: m.feedback ?? null,
                }))
            : [];

        // Show the sent message even if the server echoed nothing usable, so the
        // conversation never looks like it swallowed the input.
        const toAppend: Message[] = returned.length > 0 ? returned : [{
            id: Date.now(),
            content: message,
            sender: 'user',
            timestamp: new Date().toISOString(),
            feedback: null,
        }];

        setHistories(prev => {
            const existing = prev[target] || { id: target, title: message, messages: [] };
            return { ...prev, [target]: { ...existing, messages: [...existing.messages, ...toAppend] } };
        });

        // The question is queued; the answer is on its way over the websocket.
        startAwaitingReply(target);
    };

    const handleNewChat = async () => {
        try {
            trackAction('new_chat', 'navigation');
            
            // The route is POST /api/v1/histories?title=... There is no
            // /api/chat/history endpoint and there never has been, so this
            // always 404'd and starting a chat from the button silently failed.
            const response = await callApiWithToken(
                `api/v1/histories?title=${encodeURIComponent('New Chat')}`,
                'POST'
            );
            if (response && response.ok) {
                const newHistory = await response.json();
                
                // Track successful chat creation
                capture(AnalyticsEvents.BUTTON_CLICK, {
                    action: 'new_chart_created',
                    history_id: newHistory.id,
                    timestamp: new Date().toISOString(),
                });
                
                // Normalised on arrival rather than trusted. A conversation
                // without a messages array crashes every component that renders
                // one, and the server is not the only thing that can produce
                // this shape.
                setHistories(prev => ({
                    ...prev,
                    [newHistory.id]: { ...newHistory, messages: newHistory.messages ?? [] },
                }));
                setCurrentHistory(newHistory.id);
                
                addToast('New chat started', 'success');
            } else {
                // Track failed chat creation
                capture(AnalyticsEvents.BUTTON_CLICK, {
                    action: 'new_chat_failed',
                    error: response ? await response.text() : 'No response',
                });
                
                addToast('Failed to start new chat', 'error');
            }
        } catch (error) {
            console.error('Error starting new chat:', error);
            trackError(error as Error, { action: 'new_chat' });
            addToast('Error starting new chat', 'error');
        }
    };

    const handleDeleteHistory = async (historyIdOrObject: number | History) => {
        const historyId = typeof historyIdOrObject === 'number' ? historyIdOrObject : historyIdOrObject.id;
        const history = typeof historyIdOrObject === 'object' ? historyIdOrObject : null;
        
        // Track delete attempt
        trackAction('delete_chat', 'history', historyId.toString());
        
        try {
            // The route is DELETE /api/v1/histories?history_id=N. This called a
            // path that does not exist, so deleting a conversation always failed.
            const response = await callApiWithToken(
                `api/v1/histories?history_id=${encodeURIComponent(historyId)}`,
                'DELETE'
            );
            if (response && response.ok) {
                // Track successful deletion
                capture(AnalyticsEvents.BUTTON_CLICK, {
                    action: 'chat_deleted',
                    history_id: historyId,
                    message_count: history?.messages?.length || 0,
                    timestamp: new Date().toISOString(),
                });
                
                setHistories(prev => {
                    const newHistories = { ...prev };
                    delete newHistories[historyId];
                    return newHistories;
                });
                if (currentHistory === historyId) {
                    setCurrentHistory(null); // Or set to the latest, or none
                }
                addToast('Chat history deleted', 'success');
            } else {
                addToast('Failed to delete history', 'error');
            }
        } catch (error) {
            console.error('Error deleting history:', error);
            addToast('Error deleting history', 'error');
        }
    };

    const fetchHistories = async () => {
        try {
            // organization_id lets the backend scope conversations to the
            // workspaces this person can still see, the same answer that
            // governs documents. Without it a thread survives losing access to
            // the workspace it was held in.
            const params = new URLSearchParams();
            if (currentWorkspaceId != null) params.set('workspace_id', String(currentWorkspaceId));
            if (activeOrganizationId != null) params.set('organization_id', String(activeOrganizationId));
            const scope = params.toString() ? `?${params.toString()}` : '';
            const historiesResponse = await callApiWithToken(`api/v1/histories${scope}`, 'GET');
            if (!historiesResponse?.ok) {
                console.error('Failed to fetch chat histories:', historiesResponse?.status, historiesResponse?.statusText);
                return;
            }
            let jsonResponse;
            try {
                jsonResponse = await historiesResponse.json();
            } catch (error) {
                console.error('Error parsing JSON response:', error);
                return;
            }
            if (!Array.isArray(jsonResponse)) {
                console.error('Invalid response format:', jsonResponse);
                return;
            }
            const histories: History[] = jsonResponse.map((historyData: any) => {
                const sortedMessages = (historyData.messages || []).sort((a: any, b: any) => a.id - b.id);
                return {
                    id: historyData.id,
                    title: historyData.title,
                    messages: sortedMessages.map((messageData: any) => ({
                        id: messageData.id,
                        content: messageData.content,
                        sender: messageData.sender,
                        timestamp: messageData.timestamp,
                        feedback: messageData.feedback ?? null,
                    })),
                };
            });

            const historiesObject: { [key: number]: History } = {};
            histories.forEach((history) => {
                historiesObject[history.id] = history;
            });
            setHistories(historiesObject);
            
            if (histories.length > 0) {
                // Try to keep current history if it still exists, otherwise set to latest
                const currentIsValid = currentHistory !== null && historiesObject[currentHistory];
                if (!currentIsValid) {
                    setCurrentHistory(histories[histories.length - 1].id);
                }
            } else {
                setCurrentHistory(null); // No histories, so no current history
            }
            console.log('Loaded Histories:', histories);
        } catch (error) {
            console.error('Error fetching chat histories:', error);
            addToast('Could not load chat histories.', 'error');
        }
    };

    const handleLogout = async () => {
        try {
            trackAction('logout_click', 'authentication');
            const posthog = getPosthog();
            if (posthog?.flush) {
                await posthog.flush();
            }
            if (posthog?.reset) {
                posthog.reset();
            }
            capture(AnalyticsEvents.USER_LOGOUT, {
                user_id: user?.uid,
                email: user?.email,
                session_duration: user?.metadata?.lastSignInTime
                    ? Date.now() - new Date(user.metadata.lastSignInTime).getTime()
                    : null,
            });
            onLogout();
            setTimeout(() => {
                navigate('/');
            }, 200);
        } catch (error) {
            console.error('Error during logout:', error);
        }
        setSelectedFile(null);
    };

    const handleDeleteFile = useCallback(async (fileId: number) => {
        trackAction(AnalyticsEvents.FILE_DELETE_INITIATED, 'file_management', `file_id: ${fileId}`);
        try {
            await deleteFileFromContext(fileId, currentWorkspaceId ?? ALL_WORKSPACES);
            addToast('File deleted successfully!', 'success');
        } catch (error) {
            addToast('Failed to delete file.', 'error');
            console.error('Error deleting file:', error);
        }
    }, [deleteFileFromContext, addToast, currentWorkspaceId]);

    const handleFileClick = useCallback(async (file: UploadedFile) => {
        trackAction(AnalyticsEvents.FILE_VIEW_CLICKED, 'file_management', `file_id: ${file.id}, name: ${file.file_name}, type: ${file.file_type}`);
        // file_url identifies the document but does not grant access to it, so
        // the viewer needs a URL minted for this user, for this view.
        const response = await callApiWithToken(`api/v1/files/${file.id}/access-url`, 'GET');
        const data = response?.ok ? await response.json().catch(() => null) : null;
        if (!data?.url) {
            addToast(`Could not open ${file.file_name}.`, 'error');
            return;
        }
        setSelectedFile({ ...file, file_url: data.url });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [setSelectedFile, addToast]);

    const handleCloseFileViewer = () => {
                if (selectedFile) {
            trackAction(AnalyticsEvents.FILE_VIEW_CLOSED, 'file_management', `file_id: ${selectedFile.id}`);
        }
        setSelectedFile(null);
    };

    if (!user && authLoading) {
        return <div className="loading-container"><div className="spinner"></div><p>Loading user session...</p></div>;
    }
    if (!user) {
        return <Navigate to="/login" replace />;
    }

    return (
        <div className={`chat-app-container ${darkMode ? 'dark-mode' : ''}`}>
            {isMobile && (
                <Tabs value={activeTab} onValueChange={setActiveTab} className="mobile-tabs">
                    <TabsList className="w-full">
                        <TabsTrigger value="knowledge">Knowledge</TabsTrigger>
                        <TabsTrigger value="chat">Chat</TabsTrigger>
                        <TabsTrigger value="history">History</TabsTrigger>
                    </TabsList>
                </Tabs>
            )}
            <div className="layout-container">
                <aside className={`sidebar-left knowledge-column ${activeTab === 'knowledge' ? 'active' : ''}`}>
                    <KnowledgeBaseComponent
                        // handleFileClick was defined and never passed: the list
                        // set the raw record straight into state, so its
                        // analytics never fired and, now, its URL would not be
                        // readable.
                        onFileClick={handleFileClick}
                        darkMode={darkMode}
                        onWorkspaceChange={setCurrentWorkspaceId}
                    />
                    <div className="settings-button-container">
                        {/* Who am I, and where am I? Both matter once somebody
                            can belong to a company they do not own: an invited
                            member and the owner see the same screen otherwise,
                            and a shared machine makes it worse. */}
                        {user && (
                            <div className="signed-in-as">
                                <div className="signed-in-name" title={user.email || undefined}>
                                    {user.displayName || user.email}
                                </div>
                                {orgContext?.name && (
                                    <div className="signed-in-org">
                                        {orgContext.name}
                                        {orgContext.role && orgContext.role !== 'owner' && (
                                            /* The role, by its name. This said
                                               "can manage" and "read only",
                                               which described the same thing a
                                               third way: the chooser called it
                                               Member and the members list called
                                               it Can read only. One role should
                                               not have three names, least of all
                                               when the database, the capability
                                               table and the docs all agree on
                                               one. */
                                            <span className="signed-in-role">
                                                {' · '}
                                                {orgContext.role === 'admin' ? 'Admin' : 'Staff'}
                                            </span>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}
                        <Button onClick={handleSettingsClick} variant="outline" className="w-full">
                            ⚙️ Settings
                        </Button>
                        {/* Escape hatch for the rare person who belongs to more
                            than one company, so switching does not require
                            signing out. Hidden for everyone else. */}
                        {hasMultipleOrganizations && (
                            <Button
                                onClick={() => navigate('/select-organization')}
                                variant="ghost"
                                className="w-full"
                            >
                                Switch organization
                            </Button>
                        )}
                    </div>
                </aside>

                <main className={`main-content-area chat-column ${activeTab === 'chat' ? 'active' : ''}`}>
                    {composerMode === 'find' ? (
                        <SearchResults
                            query={searchQuery}
                            results={searchResults}
                            isSearching={isSearching}
                            hasSearched={hasSearched}
                            files={userFiles}
                        />
                    ) : (
                        <ConversationView
                            files={userFiles}
                            history={currentHistory !== null && histories[currentHistory] ? histories[currentHistory] : null}
                            awaitingReply={awaitingReplyFor !== null && awaitingReplyFor === currentHistory}
                            onCopy={handleCopy}
                            onFeedbackChange={handleFeedbackChange}
                        />
                    )}
                    <InputArea
                        onSend={handleSend}
                        isSending={isSending || isSearching}
                        mode={composerMode}
                        onModeChange={setComposerMode}
                        workspaceId={typeof currentWorkspaceId === 'number' ? currentWorkspaceId : null}
                        onContentAdded={async () => {
                            // Scoped to the workspace the file was uploaded into.
                        // Reloading without it showed every document in the
                        // organization, including other workspaces'.
                        await loadUserFiles(1, filePagination.pageSize, currentWorkspaceId ?? ALL_WORKSPACES);
                        }}
                    />
                </main>

                <aside className={`sidebar-right history-column ${activeTab === 'history' ? 'active' : ''}`}>
                    <div className="logout-button-container">
                        <Button onClick={handleLogout} variant="outline">Logout</Button>
                        <WebSocketStatusIndicator />
                    </div>
                    <HistoryView
                        histories={Object.values(histories)}
                        setCurrentHistory={setCurrentHistory}
                        onNewChat={handleNewChat}
                        onDeleteHistory={handleDeleteHistory}
                    />
                </aside>
            </div>

            {selectedFile && (
                <FileViewerComponent
                    file={selectedFile!}
                    onClose={handleCloseFileViewer}
                    darkMode={darkMode}
                    onError={(error) => console.error(`File ${selectedFile.id} processing error:`)}
                />
            )}
        </div>
    );
};

export default ChatApp;
