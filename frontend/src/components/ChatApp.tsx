import React, { useEffect, useState, useCallback } from 'react';
import { toast, ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import { useNavigate } from 'react-router-dom';
import { useAnalytics, usePostHog, trackFeatureUsage } from '../components/AnalyticsProvider';
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

interface ChatAppProps {
    user: User | null;
    onLogout: () => void;
}

const ChatApp: React.FC<ChatAppProps> = ({ user, onLogout }) => {
    // Initialize analytics
    const analytics = useAnalytics();
    const posthog = usePostHog();
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
    const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null);
    const [idToken, setIdToken] = useState<string | null>(null); // State for ID token
    const navigate = useNavigate();
    const [activeTab, setActiveTab] = useState("chat"); // Default to "chat"
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
    const { socket } = useUserContext();
    const [isSending, setIsSending] = useState(false);
    const [sessionStartTime] = useState(Date.now()); // Track when the session started

    const SOCKET_RECONNECTION_ATTEMPTS = 5;
    const SOCKET_RECONNECTION_DELAY = 3000;
    const SOCKET_RECONNECTION_DELAY_MAX = 15000;

    useEffect(() => {
        const handleResize = () => {
            const newIsMobile = window.innerWidth <= 768;
            if (newIsMobile !== isMobile) {
                // Track viewport change
                posthog.capture('viewport_changed', {
                    is_mobile: newIsMobile,
                    viewport_width: window.innerWidth
                });
            }
            setIsMobile(newIsMobile);
        };
        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, [isMobile]);

    // Track component mount
    useEffect(() => {
        // Track chat app loaded event
        posthog.capture('chat_app_loaded', {
            has_user: !!user,
            user_id: user?.uid,
            is_mobile: isMobile,
            subscription_status: subscriptionStatus
        });
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
        // Track settings navigation
        posthog.capture('navigate_to_settings', {
            from_page: 'chat_app'
        });
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
        navigator.clipboard.writeText(message.content);
        toast.success('Copied to clipboard!');
        
        // Track copy message action
        posthog.capture('copy_message', {
            message_type: message.sender,
            message_length: message.content.length,
            history_id: currentHistory
        });
    };

    const handleSend = async (message: string, files: File[]) => {
        if (!message.trim() && files.length === 0) return;
        
        // Track message send attempt
        posthog.capture('message_send', {
            has_text: !!message.trim(),
            has_files: files.length > 0,
            file_count: files.length,
            history_id: currentHistory
        });
        
        setIsSending(true);
        
        if (message.trim()) {
            await sendMessage(message);
        }

        if (files.length > 0) {
            const formData = new FormData();
            for (let i = 0; i < files.length; i++) {
                formData.append("files", files[i]);
            }
            const fileDataResponse = await callApiWithToken(
                `api/v1/files?language=${encodeURIComponent(selectedLanguage)}&comprehensionLevel=${encodeURIComponent(comprehensionLevel)}`,
                'POST',
                formData
            );
            if (fileDataResponse && fileDataResponse.ok) {
                const fileData = await fileDataResponse.json();
                toast.success(fileData.message, { position: 'top-right', autoClose: 3000, hideProgressBar: false, closeOnClick: true, pauseOnHover: true, draggable: true, progress: undefined });
                fetchUserFiles();
            } else {
                toast.error('File upload failed. Please try again.', { position: 'top-right', autoClose: 5000, hideProgressBar: false, closeOnClick: true, pauseOnHover: true, draggable: true, progress: undefined });
            }
        }

        setIsSending(false);
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

    const handleNewChat = () => {
        if (!idToken) return;

        // Track new chat creation
        posthog.capture('new_chat_created', {
            previous_history_id: currentHistory
        });

        // Make API request to create new chat
        callApiWithToken('/api/v1/history', 'POST')
            .then(response => response && response.ok ? response.json() : Promise.reject('Failed to create new chat'))
            .then(data => {
                // Add new history to the list
                const newHistoryId = data.id;
                const newHistory = {
                    id: newHistoryId,
                    title: 'New Chat',
                    messages: [],
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString(),
                };

                setHistories(prev => ({
                    ...prev,
                    [newHistoryId]: newHistory,
                }));
                setCurrentHistory(newHistoryId);
            })
            .catch(error => {
                console.error('Error creating new chat:', error);
                
                // Track error
                posthog.capture('new_chat_error', {
                    error: error.message || 'Unknown error'
                });
            });
    };

    const handleDeleteHistory = async (historyIdOrObject: number | History) => {
        // Track history deletion
        posthog.capture('history_deleted', {
            history_id: typeof historyIdOrObject === 'number' ? historyIdOrObject : historyIdOrObject.id
        });
        const historyId = typeof historyIdOrObject === 'number' ? historyIdOrObject : historyIdOrObject.id;
        try {
            const response = await callApiWithToken(`/api/chat/history/${historyId}`, 'DELETE');
            if (response && response.ok) {
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
            // Track logout action with session data
            posthog.capture('logout', {
                method: 'button_click',
                page: 'chat_app',
                histories_count: Object.keys(histories).length,
                files_count: knowledgeBaseFiles.length,
                session_duration: Math.floor((Date.now() - sessionStartTime) / 1000)
            });
            
            // Optional: flush events before logout to ensure they're sent
            if (typeof posthog.flush === 'function') {
                await posthog.flush();
            }
            
            // Call the provided onLogout function
            onLogout();
        } catch (error) {
            console.error('Error during logout:', error);
        }
    };

    const handleFileError = (error: string) => {
        setSelectedFile(null);
    };

    const handleFileClick = (file: UploadedFile) => {
        // Update the state with the selected file
        setSelectedFile(file);
        
        // Track file view event
        posthog.capture('file_viewed', {
            file_id: file.id,
            file_name: file.name,
            file_type: file.type
        });
        
        // If on mobile, switch to file view tab
        if (isMobile) {
            setActiveTab("files");
        }
    };

    const handleCloseFileViewer = () => {
        // Track file viewer closed
        if (selectedFile) {
            posthog.capture('file_viewer_closed', {
                file_id: selectedFile.id,
                file_name: selectedFile.name
            });
        }
        setSelectedFile(null);
    };

    const fetchUserFiles = useCallback(async () => {
        console.log("Fetching user files...");
        if (!user || !idToken) return; // Ensure user and token are available
        try {
            const response = await callApiWithToken('/api/v1/files', 'GET');
            if (response && response.ok) {
                const filesData: UploadedFile[] = await response.json();
                console.log("Files received:", filesData);
                setKnowledgeBaseFiles(filesData);
            } else {
                console.error('Failed to fetch files:', response ? response.statusText : 'No response');
                setKnowledgeBaseFiles([]); // Clear files on failure
            }
        } catch (error) {
            console.error('Error fetching user files:', error);
            setKnowledgeBaseFiles([]); // Clear files on error
        }
    }, [user, idToken]); // Added dependencies

    // Fetch files initially and when user/token changes
    useEffect(() => {
        fetchUserFiles();
    }, [fetchUserFiles]);

    const handleDeleteFile = async (fileId: number) => {
        // Find file to track deletion details
        const fileToDelete = knowledgeBaseFiles.find(file => file.id === fileId);
        
        // Track file deletion in PostHog
        if (fileToDelete) {
            posthog.capture('file_deleted', {
                file_id: fileId,
                file_name: fileToDelete.name,
                file_type: fileToDelete.type
            });
        }
        if (!user) {
            console.error('User is not available.');
            return;
        }
        try {
            const token = await user.getIdToken();
            const deleteResponse = await fetch(`api/v1/files/${fileId}`, { method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` } });
            if (deleteResponse.ok) {
                setKnowledgeBaseFiles((prevFiles) => prevFiles.filter((file) => file.id !== fileId));
            } else {
                console.error('Failed to delete file:', deleteResponse.statusText);
            }
        } catch (error) {
            console.error('Error deleting file:', error);
        }
    };

    const renderTabContent = () => {
        switch (activeTab) {
            case "files":
                return (
                    <div className={`files-column ${activeTab === "files" ? "active" : ""}`}>
                        <KnowledgeBaseComponent
                            token={idToken ?? ''}
                            fetchFiles={fetchUserFiles}
                            files={knowledgeBaseFiles}
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
                                files={knowledgeBaseFiles}
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
