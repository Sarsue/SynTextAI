import React, { useEffect, useState } from 'react';
import { toast, ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import { useNavigate } from 'react-router-dom';
import ConversationView from './ConversationView';
import InputArea from './InputArea';
import HistoryView from './HistoryView';
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
    const [histories, setHistories] = useState<{ [key: number]: History }>({});
    const [currentHistory, setCurrentHistory] = useState<number | null>(null);
    const { darkMode, userSettings, fetchSubscriptionStatus, subscriptionStatus } = useUserContext();
    const [selectedLanguage] = useState<string>(userSettings.selectedLanguage || '');
    const [comprehensionLevel] = useState<string>(userSettings.comprehensionLevel || '');
    const [knowledgeBaseFiles, setKnowledgeBaseFiles] = useState<UploadedFile[]>([]);
    const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null);
    const { isPollingFiles, setIsPollingFiles } = useUserContext();
    const { isPollingMessages, setIsPollingMessages } = useUserContext();
    const navigate = useNavigate();
    const parentIsPollingMessages = isPollingMessages;
    const [activeTab, setActiveTab] = useState("chat"); // Default to "chat"
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);

    useEffect(() => {
        const handleResize = () => {
            setIsMobile(window.innerWidth <= 768);
        };
        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, []);

    const handleSettingsClick = () => {
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
        navigator.clipboard.writeText(textToCopy)
            .then(() => { console.log('Text successfully copied to clipboard:', textToCopy); })
            .catch((err) => { console.error('Unable to copy text to clipboard:', err); });
    };

    const handleSend = async (message: string, files: File[]) => {
        try {
            console.log('Files to append:', files);
            if (files.length > 0) {
                const formData = new FormData();
                for (let i = 0; i < files.length; i++) {
                    formData.append(files[i].name, files[i]);
                }
                const fileDataResponse = await callApiWithToken(
                    `api/v1/files?language=${encodeURIComponent(selectedLanguage)}&comprehensionLevel=${encodeURIComponent(comprehensionLevel)}`,
                    'POST',
                    formData
                );
                if (fileDataResponse && fileDataResponse.ok) {
                    const fileData = await fileDataResponse.json();
                    toast.success(fileData.message, { position: 'top-right', autoClose: 3000, hideProgressBar: false, closeOnClick: true, pauseOnHover: true, draggable: true, progress: undefined });
                    setIsPollingFiles(true);
                    fetchUserFiles();
                } else {
                    toast.error('File upload failed. Please try again.', { position: 'top-right', autoClose: 5000, hideProgressBar: false, closeOnClick: true, pauseOnHover: true, draggable: true, progress: undefined });
                }
                if (!message.trim()) { return; }
            }
            if (currentHistory !== null && !isNaN(currentHistory)) {
                await sendMessage(message);
            } else {
                await sendMessage(message);
            }
        } catch (error) {
            console.error('Error sending message:', error);
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
                    setIsPollingMessages(true);
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
                        setIsPollingMessages(true);
                    }
                }
            }
        }
    };

    const handleClearHistory = async () => {
        try {
            const clearHistoryResponse = await callApiWithToken(`api/v1/histories/all`, 'DELETE');
            if (!clearHistoryResponse?.ok) {
                console.error('Failed to clear history:', clearHistoryResponse?.statusText);
                return;
            }
            await fetchHistories();
            setCurrentHistory(null);
        } catch (error) {
            console.error('Error clearing history:', error);
        }
    };

    const handleNewChat = async () => {
        try {
            const createHistoryResponse = await callApiWithToken(`api/v1/histories?title=New%20Chat`, 'POST');
            if (createHistoryResponse) {
                const createHistoryData = createHistoryResponse.ok ? await createHistoryResponse.json() : null;
                if (createHistoryData?.id) {
                    const newHistory: History = { id: createHistoryData.id, title: createHistoryData.title, messages: [] };
                    setHistories((prevHistories) => ({ ...prevHistories, [newHistory.id]: newHistory }));
                    setCurrentHistory(newHistory.id);
                }
            }
        } catch (error) {
            console.error('Error creating new chat:', error);
        }
    };

    const handleDeleteHistory = async (historyId: number | History) => {
        try {
            const idToDelete = typeof historyId === 'number' ? historyId : historyId.id;
            const deleteHistoryResponse = await callApiWithToken(`api/v1/histories?history-id=${encodeURIComponent(idToDelete)}`, 'DELETE');
            if (deleteHistoryResponse) {
                const deleteHistoryData = await deleteHistoryResponse.json();
                if (deleteHistoryData?.message === 'History deleted successfully') {
                    console.log('History deleted successfully');
                    await fetchHistories();
                    if (currentHistory === idToDelete) {
                        setCurrentHistory(null);
                    }
                } else {
                    console.error('Failed to delete history:', deleteHistoryData?.message);
                }
            }
        } catch (error) {
            console.error('Error deleting history:', error);
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
            if (isPollingMessages && latestHistoryId !== null) {
                const lastMessage = histories[histories.length - 1]?.messages?.slice(-1)[0];
                if (lastMessage?.sender === 'bot') {
                    setIsPollingMessages(false);
                }
            }
        } catch (error) {
            console.error('Error fetching chat histories:', error);
        }
    };

    const handleLogout = () => {
        onLogout();
        navigate('/');
    };

    const handleDownloadHistory = async () => {
        if (!Object.keys(histories).length) {
            console.error('No chat histories available to download.');
            toast.error('No chat histories available to download.', { position: 'top-right', autoClose: 5000, hideProgressBar: false, closeOnClick: true, pauseOnHover: true, draggable: true, progress: undefined });
            return;
        }
        try {
            const historiesArray = Object.values(histories);
            const dataStr = JSON.stringify(historiesArray, null, 2);
            const blob = new Blob([dataStr], { type: 'application/json' });
            const username = user?.displayName;
            const url = URL.createObjectURL(blob);
            const now = new Date();
            const datetimeString = now.toISOString().replace(/[:.]/g, '-');
            const filename = `${username}_${datetimeString}_history.json`;
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            toast.success('Chat histories downloaded successfully!', { position: 'top-right', autoClose: 3000, hideProgressBar: false, closeOnClick: true, pauseOnHover: true, draggable: true, progress: undefined });
        } catch (error) {
            console.error('Error downloading chat histories:', error);
            toast.error('Error downloading chat histories. Please try again.', { position: 'top-right', autoClose: 5000, hideProgressBar: false, closeOnClick: true, pauseOnHover: true, draggable: true, progress: undefined });
        }
    };

    const handleFileError = (error: string) => {
        setSelectedFile(null);
    };

    const handleFileClick = (file: UploadedFile) => {
        setSelectedFile(file);
    };

    const handleCloseFileViewer = () => {
        setSelectedFile(null);
    };

    useEffect(() => {
        let pollingInterval: NodeJS.Timeout | null = null;
        const fetchInitialData = async () => {
            try {
                await fetchHistories();
                await fetchUserFiles();
            } catch (error) {
                console.error('Error fetching initial data:', error);
            }
        };
        const pollData = async () => {
            try {
                if (isPollingMessages) {
                    await fetchHistories();
                }
                if (isPollingFiles) {
                    await fetchUserFiles();
                }
            } catch (error) {
                console.error('Error during polling:', error);
            }
        };
        if (user) {
            fetchInitialData();
            pollingInterval = setInterval(pollData, 30000);
        }
        return () => {
            if (pollingInterval) clearInterval(pollingInterval);
        };
    }, [user, isPollingMessages, isPollingFiles]);

    const fetchUserFiles = async () => {
        if (!user) return;
        try {
            const token = await user.getIdToken();
            const response = await fetch(`api/v1/files`, { headers: { 'Authorization': `Bearer ${token}` } });
            if (response.ok) {
                const files: UploadedFile[] = await response.json();
                setKnowledgeBaseFiles(files);
                if (isPollingFiles) {
                    const unprocessedFiles = files.some((file) => !file.processed);
                    if (!unprocessedFiles) {
                        setIsPollingFiles(false);
                    }
                }
            } else {
                console.error('Failed to fetch user files:', response.statusText);
            }
        } catch (error) {
            console.error('Error fetching user files:', error);
        }
    };

    const handleDeleteFile = async (fileId: number) => {
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
                            files={knowledgeBaseFiles}
                            onDeleteFile={handleDeleteFile}
                            onFileClick={handleFileClick}
                            darkMode={darkMode}
                        />
                        {selectedFile && (
                            <FileViewerComponent
                                fileUrl={selectedFile.publicUrl}
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
                            history={currentHistory !== null ? histories[currentHistory] : null}
                            onCopy={handleCopy}
                        />
                        <InputArea onSend={handleSend} isSending={parentIsPollingMessages} />
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
                            onClearHistory={handleClearHistory}
                            onNewChat={handleNewChat}
                            onDeleteHistory={handleDeleteHistory}
                            onDownloadHistory={handleDownloadHistory}
                        />
                    </div>
                );
            default:
                return null;
        }
    };

    return (
        <div className={`chat-app-container ${darkMode ? "dark-mode" : ""}`}>
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
                                files={knowledgeBaseFiles}
                                onDeleteFile={handleDeleteFile}
                                onFileClick={handleFileClick}
                                darkMode={darkMode}
                            />
                            {selectedFile && (
                                <FileViewerComponent
                                    fileUrl={selectedFile.publicUrl}
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
                                history={currentHistory !== null ? histories[currentHistory] : null}
                                onCopy={handleCopy}
                            />
                            <InputArea onSend={handleSend} isSending={parentIsPollingMessages} />
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
                                onClearHistory={handleClearHistory}
                                onNewChat={handleNewChat}
                                onDeleteHistory={handleDeleteHistory}
                                onDownloadHistory={handleDownloadHistory}
                            />
                        </div>
                    </>
                )}
            </div>
        </div>
    );
};

export default ChatApp;
