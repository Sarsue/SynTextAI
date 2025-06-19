
import React, { useEffect, useState, useCallback, useRef } from 'react';
import { toast, ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import { useNavigate, useLocation } from 'react-router-dom';
import ConversationView from './ConversationView';
import InputArea from './InputArea';
import HistoryView from './HistoryView';
import VoiceInput from './VoiceInput';
import { Message, History } from '../components/types';
import './ChatApp.css';
import { User } from 'firebase/auth';
import { useUserContext } from '../UserContext';
import KnowledgeBaseComponent from './KnowledgeBaseComponent';
import FileViewerComponent from './FileViewerComponent';
import { Persona, UploadedFile } from './types';
import Tabs from "./Tabs";
import useAnalytics from '../hooks/useAnalytics';
import { AnalyticsEvents, createEventProperties } from '../utils/analyticsEvents';
import { trackPageView, trackAction, trackError, getPosthog } from '../utils/analyticsQueue';

interface ChatAppProps {
    user: User | null;
    onLogout: () => void;
}

const ChatApp: React.FC<ChatAppProps> = ({ user, onLogout }) => {
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

    // --- YouTube link upload state ---
    const [youtubeUrl, setYoutubeUrl] = useState('');
    const [isYoutubeSubmitting, setIsYoutubeSubmitting] = useState(false);
    const [youtubeError, setYoutubeError] = useState('');
    const [histories, setHistories] = useState<{ [key: number]: History }>({});
    const [currentHistory, setCurrentHistory] = useState<number | null>(null);
    const { darkMode, userSettings, fetchSubscriptionStatus, subscriptionStatus } = useUserContext();
    const [selectedLanguage] = useState<string>(userSettings.selectedLanguage || '');
    const [comprehensionLevel] = useState<string>(userSettings.comprehensionLevel || '');
    const [knowledgeBaseFiles, setKnowledgeBaseFiles] = useState<UploadedFile[]>([]);
    interface PaginationState {
        page: number;
        pageSize: number;
        totalItems: number;
    }

    const [pagination, setPagination] = useState<PaginationState>({
        page: 1,
        pageSize: 10,
        totalItems: 0
    });
    
    const handlePageChange = useCallback((newPage: number) => {
        setPagination(prev => ({
            ...prev,
            page: newPage
        }));
    }, []);
    
    const handlePageSizeChange = useCallback((newSize: number) => {
        setPagination(prev => ({
            ...prev,
            page: 1,
            pageSize: newSize
        }));
    }, []);
    const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null);
    const [idToken, setIdToken] = useState<string | null>(null); // State for ID token
    const navigate = useNavigate();
    const [activeTab, setActiveTab] = useState("chat"); // Default to "chat"
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
    const { socket } = useUserContext();
    const [isSending, setIsSending] = useState(false);

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

    // Effect to get ID token
    useEffect(() => {
        const fetchToken = async () => {
            if (user) {
                try {
                    const token = await user.getIdToken();
                    setIdToken(token);
                } catch (error) {
                    console.error('Error fetching ID token:', error);
                    setIdToken(null); // Handle error case
                }
            } else {
                setIdToken(null); // Clear token if no user
            }
        };
        fetchToken();
    }, [user]);

    const handleSettingsClick = () => {
        trackAction('settings_click', 'navigation');
        navigate('/settings');
    };

    const callApiWithToken = async (url: string, method: string, body?: any) => {
        try {
            const idToken = await user?.getIdToken();
            if (!idToken) {
                console.error('User token not available');
                return null;
            }
            const headers: HeadersInit = { 'Authorization': `Bearer ${idToken}` };
            const response = await fetch(url, { method, headers, mode: 'cors', credentials: 'include', body: body ? body : undefined });
            return response;
        } catch (error) {
            console.error('Unexpected error calling API:', error);
            return null;
        }
    };

    const handleCopy = (message: Message) => {
        const textToCopy = message.content;
        trackAction('copy_message', 'message', undefined, textToCopy.length);
        navigator.clipboard.writeText(textToCopy)
            .then(() => { 
                if (process.env.NODE_ENV === 'development') {
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
            setIsSending(true); // Disable input while processing
            
            // Track the send action
            capture(AnalyticsEvents.CHAT_MESSAGE_SENT, {
                message_length: message.length,
                has_attachments: files.length > 0,
                file_count: files.length,
                file_types: files.map(file => file.type),
                language: selectedLanguage,
                comprehension_level: comprehensionLevel,
            });

            if (process.env.NODE_ENV === 'development') {
                console.log('Files to append:', files);
            }
            
            if (files.length > 0) {
                const formData = new FormData();
                for (let i = 0; i < files.length; i++) {
                    formData.append("files", files[i]);
                }
                try {
                    const startTime = Date.now();
                    const fileDataResponse = await callApiWithToken(
                        `api/v1/files?language=${encodeURIComponent(selectedLanguage)}&comprehensionLevel=${encodeURIComponent(comprehensionLevel)}`,
                        'POST',
                        formData
                    );
                    
                    const duration = Date.now() - startTime;
                    
                    if (fileDataResponse && fileDataResponse.ok) {
                        const fileData = await fileDataResponse.json();
                        
                        // Track successful file upload
                        capture(AnalyticsEvents.FILE_UPLOAD, {
                            success: true,
                            file_count: files.length,
                            file_types: files.map(file => file.type),
                            total_size: files.reduce((acc, file) => acc + file.size, 0),
                            upload_duration_ms: duration,
                            language: selectedLanguage,
                            comprehension_level: comprehensionLevel,
                        });
                        
                        toast.success(fileData.message, { 
                            position: 'top-right', 
                            autoClose: 3000, 
                            hideProgressBar: false, 
                            closeOnClick: true, 
                            pauseOnHover: true, 
                            draggable: true, 
                            progress: undefined 
                        });
                        
                        fetchUserFiles();
                    } else {
                        // Track failed file upload
                        capture(AnalyticsEvents.FILE_UPLOAD, {
                            success: false,
                            file_count: files.length,
                            error: fileDataResponse ? await fileDataResponse.text() : 'No response',
                            status: fileDataResponse?.status,
                        });
                        
                        toast.error('File upload failed. Please try again.', { 
                            position: 'top-right', 
                            autoClose: 5000, 
                            hideProgressBar: false, 
                            closeOnClick: true, 
                            pauseOnHover: true, 
                            draggable: true, 
                            progress: undefined 
                        });
                    }
                } catch (error) {
                    // Track file upload error
                    trackError(error as Error, {
                        action: 'file_upload',
                        file_count: files.length,
                    });
                    
                    toast.error('An error occurred during file upload. Please try again.', { 
                        position: 'top-right', 
                        autoClose: 5000, 
                        hideProgressBar: false, 
                        closeOnClick: true, 
                        pauseOnHover: true, 
                        draggable: true, 
                        progress: undefined 
                    });
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
            setIsSending(false); // Re-enable input on error
        }
    };

    const sendMessage = async (message: string) => {
        if (currentHistory !== null && !isNaN(currentHistory)) {
            const history = histories[currentHistory];
            if (history) {
                const linkDataResponse = await callApiWithToken(
                    `api/v1/messages?message=${encodeURIComponent(message)}&history-id=${encodeURIComponent(history.id)}&language=${encodeURIComponent(selectedLanguage)}&comprehensionLevel=${encodeURIComponent(comprehensionLevel)}`,
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
                    const linkDataResponse = await callApiWithToken(
                        `api/v1/messages?message=${encodeURIComponent(message)}&history-id=${encodeURIComponent(newHistory.id)}&language=${encodeURIComponent(selectedLanguage)}&comprehensionLevel=${encodeURIComponent(comprehensionLevel)}`,
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
                
                toast.success('New chat started');
            } else {
                // Track failed chat creation
                capture(AnalyticsEvents.BUTTON_CLICK, {
                    action: 'new_chat_failed',
                    error: response ? await response.text() : 'No response',
                });
                
                toast.error('Failed to start new chat');
            }
        } catch (error) {
            console.error('Error starting new chat:', error);
            trackError(error as Error, { action: 'new_chat' });
            toast.error('Error starting new chat');
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
                toast.success('Chat history deleted');
            } else {
                toast.error('Failed to delete history');
            }
        } catch (error) {
            console.error('Error deleting history:', error);
            toast.error('Error deleting history');
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
            const latestHistoryId = histories.length > 0 ? histories[histories.length - 1].id : null;
            setCurrentHistory(latestHistoryId);
            console.log('Loaded Histories:', histories);
        } catch (error) {
            console.error('Error fetching chat histories:', error);
        }
    };

    const handleLogout = async () => {
        try {
            // Track logout attempt
            trackAction('logout_click', 'authentication');
            
            // Get the posthog instance
            const posthog = getPosthog();
            
            // Flush any pending analytics events
            if (posthog?.flush) {
                await posthog.flush();
            }
            
            // Reset user session in analytics
            if (posthog?.reset) {
                posthog.reset();
            }
            
            // Track successful logout
            capture(AnalyticsEvents.USER_LOGOUT, {
                user_id: user?.uid,
                email: user?.email,
                session_duration: user?.metadata?.lastSignInTime 
                    ? Date.now() - new Date(user.metadata.lastSignInTime).getTime() 
                    : null,
            });
            
            // Perform the actual logout
            onLogout();
            
            // Navigate to home after a short delay to ensure events are sent
            setTimeout(() => {
                navigate('/');
            }, 200);
            
        } catch (error) {
            console.error('Error during logout:', error);
            trackError(error as Error, { action: 'logout' });
            
            // Still proceed with logout even if analytics fails
            onLogout();
            navigate('/');
        }
    };


    const handleDeleteFile = async (fileId: number) => {
        // Track delete attempt
        trackAction('delete_file', 'file_management', fileId.toString());
        
        try {
            const response = await callApiWithToken(`/api/v1/files/${fileId}`, 'DELETE');
            
            if (response && response.ok) {
                // Track successful deletion
                const file = knowledgeBaseFiles.find(f => f.id === fileId);
                capture(AnalyticsEvents.FILE_DELETE, {
                    file_id: fileId,
                    file_name: file?.name || 'unknown',
                    file_type: file?.type || 'unknown',
                    file_size: file?.size || 0,
                    processing_status: file?.processing_status || 'unknown',
                });
                
                // Remove file from state
                setKnowledgeBaseFiles(prevFiles => prevFiles.filter(file => file.id !== fileId));
                
                // If deleted file is currently selected, clear selection
                if (selectedFile?.id === fileId) {
                    setSelectedFile(null);
                }
                
                toast.success('File deleted successfully');
                return true;
            } else {
                // Track failed deletion
                capture(AnalyticsEvents.ERROR, {
                    action: 'file_deletion_failed',
                    file_id: fileId,
                    status: response?.status,
                    error: response ? await response.text() : 'No response',
                });
                
                toast.error('Failed to delete file');
                return false;
            }
        } catch (error) {
            // Track deletion error
            trackError(error as Error, { 
                action: 'delete_file',
                file_id: fileId,
            });
            
            toast.error('An error occurred while deleting the file');
            return false;
        }
    };

    const handleFileError = (error: string) => {
        // Track file viewing error
        if (selectedFile) {
            capture(AnalyticsEvents.ERROR, {
                action: 'file_view_error',
                file_id: selectedFile.id,
                file_name: selectedFile.name,
                file_type: selectedFile.type,
                error: error,
            });
        }
        setSelectedFile(null);
    };

    const handleFileClick = (file: UploadedFile) => {
        // Track file view
        capture(AnalyticsEvents.FILE_VIEW, {
            file_id: file.id,
            file_name: file.name,
            file_type: file.type,
            file_size: file.size || 0,
            processing_status: file.processing_status,
        });
        
        // Set view start time for tracking view duration
        const fileWithViewTime = {
            ...file,
            viewStartTime: Date.now(),
            processing_status: file.processing_status
        };
        
        setSelectedFile(fileWithViewTime);
        
        // Track view of unprocessed or failed files
        if (file.processing_status !== 'processed') {
            capture(AnalyticsEvents.FEATURE_USAGE, {
                action: `view_${file.processing_status || 'unknown'}_file`,
                file_id: file.id,
                file_type: file.type,
            });
        }
    };

    const handleCloseFileViewer = () => {
        if (selectedFile) {
            // Track file viewer close event
            capture(AnalyticsEvents.BUTTON_CLICK, {
                action: 'close_file_viewer',
                file_id: selectedFile.id,
                file_name: selectedFile.name,
                view_duration_ms: selectedFile.viewStartTime 
                    ? Date.now() - selectedFile.viewStartTime 
                    : null,
            });
        }
        setSelectedFile(null);
    };

    const fetchUserFiles = useCallback(async (page: number = 1, pageSize: number = 10): Promise<{ items: UploadedFile[], total: number }> => {
        if (process.env.NODE_ENV === 'development') {
            console.log(`Fetching user files... Page: ${page}, PageSize: ${pageSize}`);
        }
        
        if (!user || !idToken) {
            setKnowledgeBaseFiles([]);
            return { items: [], total: 0 };
        }
        
        try {
            const startTime = Date.now();
            const response = await callApiWithToken(
                `/api/v1/files?page=${page}&page_size=${pageSize}`,
                'GET'
            );
            const duration = Date.now() - startTime;
            
            // Track file fetch operation
            capture(AnalyticsEvents.FEATURE_USAGE, {
                action: 'fetch_files',
                duration_ms: duration,
                success: response?.ok === true,
                status: response?.status,
            });
            
            if (response && response.ok) {
                const data = await response.json();
                console.log("Files received:", data.items);
                
                // Update the knowledge base files in state
                setKnowledgeBaseFiles(data.items);
                setPagination(prev => ({
                    ...prev,
                    totalItems: data.total
                }));
                
                return {
                    items: data.items,
                    total: data.total
                };
            } else {
                console.error('Failed to fetch files:', response ? response.statusText : 'No response');
                setKnowledgeBaseFiles([]);
                return { items: [], total: 0 };
            }
        } catch (error) {
            console.error('Error fetching user files:', error);
            setKnowledgeBaseFiles([]);
            return { items: [], total: 0 };
        }
    }, [user, idToken, capture]); // Added dependencies

    // Fetch files initially and when user/token changes
    useEffect(() => {
        fetchUserFiles();
    }, [fetchUserFiles]);



    const renderTabContent = () => {
        switch (activeTab) {
            case "files":
                return (
                    <div className={`files-column ${activeTab === "files" ? "active" : ""}`}>
                        <KnowledgeBaseComponent
                            token={idToken ?? ''}
                            fetchFiles={fetchUserFiles}
                            onDeleteFile={handleDeleteFile}
                            onFileClick={handleFileClick}
                            darkMode={darkMode}
                        />
                        {selectedFile && (
                            <FileViewerComponent
                                file={selectedFile}
                                onClose={handleCloseFileViewer}
                                onError={handleFileError}
                                darkMode={darkMode}
                            />
                        )}
                    </div>
                );
            case "chat":
                return (
                    <div className={`conversation-column ${activeTab === "chat" ? "active" : ""}`}>
                        {user !== null && (
                            <div className="logout-button-container">
                                <button onClick={handleLogout}>Logout</button>
                            </div>
                        )}
                        <ConversationView
                            files={knowledgeBaseFiles}
                            history={currentHistory !== null ? histories[currentHistory] : null}
                            onCopy={handleCopy}
                        />
                        <InputArea
                            onSend={handleSend}
                            isSending={isSending}
                            token={idToken}
                            onContentAdded={fetchUserFiles}
                        />
                        {/* Settings Button at the bottom left */}
                        {user !== null && (
                            <button
                                className="settings-button"
                                onClick={handleSettingsClick}
                                style={{
                                    backgroundColor: darkMode ? 'var(--button-bg)' : 'var(--light-bg)',
                                    color: darkMode ? 'var(--button-text-color)' : 'var(--light-text-color)',
                                }}
                            >
                                ⚙️
                            </button>
                        )}
                    </div>
                );
            case "history":
                return (
                    <div className={`history-column ${activeTab === "history" ? "active" : ""}`}>
                            <HistoryView
                                histories={Object.values(histories)}
                                setCurrentHistory={setCurrentHistory as React.Dispatch<React.SetStateAction<number | History | null>>}
                                onNewChat={handleNewChat}
                                onDeleteHistory={handleDeleteHistory}
                            />
                    </div>
                );
            default:
                return null;
        }
    };

    useEffect(() => {
        if (user) {
            fetchUserFiles(); // Fetch files when the component mounts or when the user changes
        }
    }, [user]); // Dependency on `user`
    useEffect(() => {
        if (socket) {
            // Handler for incoming messages
            const handleMessage = (event: MessageEvent) => {
                const data = JSON.parse(event.data);

                // Check the event type
                switch (data.event) {
                    case 'files_processed':
                        if (data.status === 'success') {
                            fetchUserFiles(); // Fetch updated files
                            toast.success(`File ${data.filename} processed successfully`);
                        } else {
                            toast.error(`Error processing file ${data.filename}: ${data.error}`);
                        }
                        break;

                    case 'message_received':
                        if (data.status === 'error') {
                            toast.error(`Error: ${data.error}`);
                            setIsSending(false);
                            return;
                        }
                        if (data.history_id && data.message) {
                            setHistories((prevHistories) => ({
                                ...prevHistories,
                                [data.history_id]: {
                                    ...prevHistories[data.history_id],
                                    messages: [...prevHistories[data.history_id].messages, data.message],
                                },
                            }));
                        }
                        setIsSending(false);
                        break;

                    default:
                        console.warn('Unknown WebSocket event:', data.event);
                        break;
                }
            };

            // Add event listener for messages
            socket.addEventListener('message', handleMessage);

            // Cleanup function
            return () => {
                socket.removeEventListener('message', handleMessage);
            };
        }
    }, [socket, fetchUserFiles]); // Add fetchUserFiles as a dependency

    return (
        <div className={`chat-app-container ${darkMode ? "dark-mode" : ""}`}>
            {/* Removed old standalone YouTube link upload UI */}
            
            {/* Main Content Layout */}
            <div className="layout-container">
                {isMobile ? (
                    <>
                        <Tabs activeTab={activeTab} setActiveTab={setActiveTab} />
                        {renderTabContent()}
                    </>
                ) : (
                    <>
                        {/* Files Column on the left */}
                        <div className="files-column">
                            <KnowledgeBaseComponent
                                token={idToken ?? ''}
                                fetchFiles={fetchUserFiles}
                                onDeleteFile={handleDeleteFile}
                                onFileClick={handleFileClick}
                                darkMode={darkMode}
                            />
                            {selectedFile && (
                                <FileViewerComponent
                                    file={selectedFile}
                                    onClose={handleCloseFileViewer}
                                    onError={handleFileError}
                                    darkMode={darkMode}
                                />
                            )}
                            {/* Settings Button at the bottom left */}
                            {user !== null && (
                                <button
                                    className="settings-button"
                                    onClick={handleSettingsClick}
                                    style={{
                                        backgroundColor: darkMode ? 'var(--button-bg)' : 'var(--light-bg)',
                                        color: darkMode ? 'var(--button-text-color)' : 'var(--light-text-color)',
                                    }}
                                >
                                    ⚙️
                                </button>
                            )}
                        </div>

                        {/* Conversation View in the middle */}
                        <div className="conversation-column">
                            <ConversationView
                                files={knowledgeBaseFiles}
                                history={currentHistory !== null ? histories[currentHistory] : null}
                                onCopy={handleCopy}
                            />
                            <InputArea
                                onSend={handleSend}
                                isSending={isSending}
                                token={idToken}
                                onContentAdded={fetchUserFiles}
                            />
                            <ToastContainer />
                        </div>

                        {/* History Column on the right */}
                        <div className="history-column">
                            {user !== null && (
                                <div className="logout-button-container">
                                    <button onClick={handleLogout}>Logout</button>
                                </div>
                            )}
                            <HistoryView
                                histories={Object.values(histories)}
                                setCurrentHistory={setCurrentHistory as React.Dispatch<React.SetStateAction<number | History | null>>}
                                onNewChat={handleNewChat}
                                onDeleteHistory={handleDeleteHistory}
                            />
                        </div>
                    </>
                )}
            </div>
        </div>
    );
};

export default ChatApp;
