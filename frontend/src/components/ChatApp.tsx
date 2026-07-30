
import React, { useEffect, useState, useCallback, useRef } from 'react';

import { useNavigate, useLocation } from 'react-router-dom';
import ConversationView from './ConversationView';
import InputArea from './InputArea';
import HistoryView from './HistoryView';
import { Message, History } from '../components/types';
import './ChatApp.css';
import { User } from 'firebase/auth';
import { useUserContext } from '../UserContext';
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
        clearIncomingChatMessage
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
                liked: false, 
                disliked: false,
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
            liked: false,
            disliked: false,
        };

        setHistories(prev => ({
            ...prev,
            [targetHistoryId]: {
                ...(prev[targetHistoryId] || { id: targetHistoryId, title: 'New Chat', messages: [] }),
                messages: [...(prev[targetHistoryId]?.messages || []), botMessage],
            },
        }));
        setIsSending(false);
        clearIncomingChatMessage();
    }, [incomingChatMessage, currentHistory, clearIncomingChatMessage]);

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
        if (!idTokenRef.current) {
            console.error('User token not available for callApiWithToken');
            addToast('Authentication session expired. Please refresh.', 'error');
            return Promise.reject(new Error('User token not available'));
        }
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

    const handleSend = async (message: string, files: File[]) => {
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
                    const fileDataResponse = await callApiWithToken(
                        `api/v1/files?language=english`,
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
                        await loadUserFiles(1, filePagination.pageSize);
                    } else {
                        capture(AnalyticsEvents.FILE_UPLOAD, {
                            success: false,
                            file_count: files.length,
                            error: fileDataResponse ? await fileDataResponse.text() : 'No response',
                            status: fileDataResponse?.status,
                        });
                        
                        addToast('File upload failed. Please try again.', 'error'); 
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
        if (currentHistory !== null && !isNaN(currentHistory)) {
            const history = histories[currentHistory];
            if (history) {
                const workspaceIdParam = currentWorkspaceId != null ? `&workspace_id=${encodeURIComponent(currentWorkspaceId)}` : '';
                const linkDataResponse = await callApiWithToken(
                    `api/v1/messages?message=${encodeURIComponent(message)}&history_id=${encodeURIComponent(history.id)}&language=english${workspaceIdParam}`,
                    'POST'
                );
                if (linkDataResponse) {
                    const linkData = await linkDataResponse.json();
                    const newMessages = Array.isArray(linkData) ? linkData.map((messageData: any) => ({
                        id: messageData.id,
                        content: messageData.content,
                        sender: messageData.sender,
                        timestamp: new Date(messageData.timestamp).toISOString(),
                        liked: messageData.is_liked === 1,
                        disliked: messageData.is_disliked === 1,
                    })) : [];
                    const updatedMessages = [...history.messages, ...newMessages];
                    const updatedHistory: History = { ...history, messages: updatedMessages };
                    setHistories((prevHistories) => ({ ...prevHistories, [currentHistory]: updatedHistory }));
                }
            }
        } else {
            const createHistoryResponse = await callApiWithToken(
                `api/v1/histories?title=${encodeURIComponent(message || 'New Chat')}`,
                'POST'
            );
            if (createHistoryResponse) {
                const createHistoryData = await createHistoryResponse.json();
                if (createHistoryData?.id) {
                    const newHistory: History = { id: createHistoryData.id, title: createHistoryData.title, messages: [] };
                    setHistories((prevHistories) => ({ ...prevHistories, [newHistory.id]: newHistory }));
                    setCurrentHistory(newHistory.id);
                    const workspaceIdParam = currentWorkspaceId != null ? `&workspace_id=${encodeURIComponent(currentWorkspaceId)}` : '';
                    const linkDataResponse = await callApiWithToken(
                        `api/v1/messages?message=${encodeURIComponent(message)}&history_id=${encodeURIComponent(newHistory.id)}&language=english${workspaceIdParam}`,
                        'POST'
                    );
                    if (linkDataResponse) {
                        const linkData = await linkDataResponse.json();
                        const newMessages = Array.isArray(linkData) ? linkData.map((messageData: any) => ({
                            id: messageData.id,
                            content: messageData.content,
                            sender: messageData.sender,
                            timestamp: new Date(messageData.timestamp).toISOString(),
                            liked: messageData.is_liked === 1,
                            disliked: messageData.is_disliked === 1,
                        })) : [];
                        const updatedMessages = [...newHistory.messages, ...newMessages];
                        setHistories((prevHistories) => ({ ...prevHistories, [newHistory.id]: { ...newHistory, messages: updatedMessages } }));
                    }
                }
            }
        }
    };

    const handleNewChat = async () => {
        try {
            trackAction('new_chat', 'navigation');
            
            const response = await callApiWithToken('/api/chat/history', 'POST');
            if (response && response.ok) {
                const newHistory = await response.json();
                
                // Track successful chat creation
                capture(AnalyticsEvents.BUTTON_CLICK, {
                    action: 'new_chart_created',
                    history_id: newHistory.id,
                    timestamp: new Date().toISOString(),
                });
                
                setHistories(prev => ({ ...prev, [newHistory.id]: newHistory }));
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
            const response = await callApiWithToken(`/api/chat/history/${historyId}`, 'DELETE');
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
            const historiesResponse = await callApiWithToken(`api/v1/histories`, 'GET');
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
                        liked: messageData.is_liked === 1,
                        disliked: messageData.is_disliked === 1,
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
            await deleteFileFromContext(fileId);
            addToast('File deleted successfully!', 'success');
        } catch (error) {
            addToast('Failed to delete file.', 'error');
            console.error('Error deleting file:', error);
        }
    }, [deleteFileFromContext, addToast]);

    const handleFileClick = useCallback((file: UploadedFile) => {
        trackAction(AnalyticsEvents.FILE_VIEW_CLICKED, 'file_management', `file_id: ${file.id}, name: ${file.file_name}, type: ${file.file_type}`);
        setSelectedFile(file);
    }, [setSelectedFile]);

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
                        onFileClick={setSelectedFile}
                        darkMode={darkMode}
                        onWorkspaceChange={setCurrentWorkspaceId}
                    />
                    <div className="settings-button-container">
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
                    <ConversationView
                        files={userFiles}
                        history={currentHistory !== null && histories[currentHistory] ? histories[currentHistory] : null}
                        onCopy={handleCopy}
                    />
                    <InputArea
                        onSend={handleSend}
                        isSending={isSending}
                        onContentAdded={async () => {
                            await loadUserFiles(1, filePagination.pageSize);
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
