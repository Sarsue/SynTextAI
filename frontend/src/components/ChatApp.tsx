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
interface ChatAppProps {
    user: User | null;
    onLogout: () => void;
    subscriptionStatus: string | null;
}

const ChatApp: React.FC<ChatAppProps> = ({ user, onLogout, subscriptionStatus }) => {
    const [loading, setLoading] = useState(false);
    const [histories, setHistories] = useState<{ [key: number]: History }>({});
    const [currentHistory, setCurrentHistory] = useState<number | null>(null);
    const { darkMode, userSettings } = useUserContext();

    // Language and comprehension level states
    const [selectedLanguage, setSelectedLanguage] = useState<string>(userSettings.selectedLanguage || '');
    const [comprehensionLevel, setComprehensionLevel] = useState<string>(userSettings.comprehensionLevel || '');
    const [knowledgeBaseFiles, setKnowledgeBaseFiles] = useState<UploadedFile[]>([]);
    const [selectedFile, setSelectedFile] = useState<UploadedFile | null>(null); // State for selected file
    const navigate = useNavigate();

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

            const headers: HeadersInit = {
                'Authorization': `Bearer ${idToken}`,
            };



            const response = await fetch(url, {
                method,
                headers,
                mode: 'cors',
                credentials: 'include',
                body: body ? body : undefined,  // Do not stringify the body for FormData
            });

            return response;
        } catch (error) {
            console.error('Unexpected error calling API:', error);
            return null;
        }
    };



    const handleCopy = (message: Message) => {
        const textToCopy = message.content;

        navigator.clipboard.writeText(textToCopy)
            .then(() => {
                console.log('Text successfully copied to clipboard:', textToCopy);
            })
            .catch((err) => {
                console.error('Unable to copy text to clipboard:', err);
            });
    };


    const handleSend = async (message: string, files: File[]) => {
        try {
            setLoading(true);
            console.log('Files to append:', files);
            console.log(subscriptionStatus);
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
                    toast.success(fileData.message, {
                        position: 'top-right',
                        autoClose: 3000,
                        hideProgressBar: false,
                        closeOnClick: true,
                        pauseOnHover: true,
                        draggable: true,
                        progress: undefined,
                    });
                    fetchUserFiles();
                } else {
                    toast.error('File upload failed. Please try again.', {
                        position: 'top-right',
                        autoClose: 5000,
                        hideProgressBar: false,
                        closeOnClick: true,
                        pauseOnHover: true,
                        draggable: true,
                        progress: undefined,
                    });
                }

                if (!message.trim()) {
                    return; // Early return if the message is empty
                }
            }

            if (currentHistory !== null && !isNaN(currentHistory)) {
                const history = histories[currentHistory];

                if (history) {
                    const temporaryMessage: Message = {
                        id: -1,
                        content: message,
                        sender: 'user',
                        timestamp: new Date().toLocaleString('en-US', { hour12: false }),
                        liked: false,
                        disliked: false,
                    };

                    const updatedMessages = [...history.messages, temporaryMessage];

                    setHistories((prevHistories) => {
                        const updatedHistory = {
                            ...prevHistories[currentHistory],
                            messages: updatedMessages,
                        };

                        return {
                            ...prevHistories,
                            [currentHistory]: updatedHistory,
                        };
                    });

                    const linkDataResponse = await callApiWithToken(
                        `api/v1/messages?message=${encodeURIComponent(message)}&history-id=${encodeURIComponent(history.id)}&language=${encodeURIComponent(selectedLanguage)}&comprehensionLevel=${encodeURIComponent(comprehensionLevel)}`,
                        'POST'
                    );

                    if (linkDataResponse) {
                        const linkData = await linkDataResponse.json();

                        const newMessages = Array.isArray(linkData)
                            ? linkData.map((messageData: any) => ({
                                id: messageData.id,
                                content: messageData.content,
                                sender: messageData.sender,
                                timestamp: messageData.timestamp,
                                liked: messageData.is_liked === 1,
                                disliked: messageData.is_disliked === 1,
                            }))
                            : [];

                        const updatedMessages = [
                            ...history.messages.filter((msg) => msg.id !== -1),
                            ...newMessages,
                        ];

                        setHistories((prevHistories) => {
                            const updatedHistory = {
                                ...prevHistories[currentHistory],
                                messages: updatedMessages,
                            };

                            return {
                                ...prevHistories,
                                [currentHistory]: updatedHistory,
                            };
                        });
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
                        const newHistory: History = {
                            id: createHistoryData.id,
                            title: createHistoryData.title,
                            messages: [],
                        };

                        const temporaryMessage: Message = {
                            id: -1,
                            content: message,
                            sender: 'user',
                            timestamp: new Date().toLocaleString('en-US', { hour12: false }),
                            liked: false,
                            disliked: false,
                        };

                        const updatedMessages = [...newHistory.messages, temporaryMessage];

                        setHistories((prevHistories) => ({
                            ...prevHistories,
                            [newHistory.id]: {
                                ...newHistory,
                                messages: updatedMessages,
                            },
                        }));

                        setCurrentHistory(newHistory.id);

                        const linkDataResponse = await callApiWithToken(
                            `api/v1/messages?message=${encodeURIComponent(message)}&history-id=${encodeURIComponent(newHistory.id)}&language=${encodeURIComponent(selectedLanguage)}&comprehensionLevel=${encodeURIComponent(comprehensionLevel)}`,
                            'POST'
                        );

                        if (linkDataResponse) {
                            const linkData = await linkDataResponse.json();

                            const newMessages = Array.isArray(linkData)
                                ? linkData.map((messageData: any) => ({
                                    id: messageData.id,
                                    content: messageData.content,
                                    sender: messageData.sender,
                                    timestamp: messageData.timestamp,
                                    liked: messageData.is_liked === 1,
                                    disliked: messageData.is_disliked === 1,
                                }))
                                : [];

                            const updatedMessages = [
                                ...newHistory.messages.filter((msg) => msg.id !== -1),
                                ...newMessages,
                            ];

                            setHistories((prevHistories) => ({
                                ...prevHistories,
                                [newHistory.id]: {
                                    ...newHistory,
                                    messages: updatedMessages,
                                },
                            }));
                        }
                    }
                }
            }
        } catch (error) {
            console.error('Error sending message:', error);
        } finally {
            setLoading(false);
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
                    const newHistory: History = {
                        id: createHistoryData.id,
                        title: createHistoryData.title,
                        messages: [],
                    };

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

            const histories: History[] = jsonResponse.map((historyData: any) => ({
                id: historyData.id,
                title: historyData.title,
                messages: (historyData.messages || []).map((messageData: any) => ({
                    id: messageData.id,
                    content: messageData.content,
                    sender: messageData.sender,
                    timestamp: messageData.timestamp,
                    liked: messageData.is_liked === 1,
                    disliked: messageData.is_disliked === 1,
                })),
            }));

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

    const handleLogout = () => {
        onLogout();
        navigate('/');
    };

    const handleDownloadHistory = async () => {
        if (!Object.keys(histories).length) {
            console.error('No chat histories available to download.');
            toast.error('No chat histories available to download.', {
                position: 'top-right',
                autoClose: 5000,
                hideProgressBar: false,
                closeOnClick: true,
                pauseOnHover: true,
                draggable: true,
                progress: undefined,
            });
            return;
        }

        try {
            const historiesArray = Object.values(histories);

            // Convert the histories object to a JSON string
            const dataStr = JSON.stringify(historiesArray, null, 2);
            const blob = new Blob([dataStr], { type: 'application/json' });
            const username = user?.displayName
            // Create a download link
            const url = URL.createObjectURL(blob);
            const now = new Date();
            const datetimeString = now.toISOString().replace(/[:.]/g, '-'); // Format as YYYY-MM-DDTHH-MM-SS
            const filename = `${username}_${datetimeString}_history.json`;
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url); // Clean up the URL object
            toast.success('Chat histories downloaded successfully!', {
                position: 'top-right',
                autoClose: 3000,
                hideProgressBar: false,
                closeOnClick: true,
                pauseOnHover: true,
                draggable: true,
                progress: undefined,
            });
        } catch (error) {
            console.error('Error downloading chat histories:', error);
            toast.error('Error downloading chat histories. Please try again.', {
                position: 'top-right',
                autoClose: 5000,
                hideProgressBar: false,
                closeOnClick: true,
                pauseOnHover: true,
                draggable: true,
                progress: undefined,
            });
        }
    };


    const handleFileError = (error: string) => {
        setSelectedFile(null);
    };

    const handleFileClick = (file: UploadedFile) => {
        setSelectedFile(file); // Set the selected file when clicked
    };
    const handleCloseFileViewer = () => {
        setSelectedFile(null); // Clear selected file when closing viewer
    };

    useEffect(() => {
        if (user) {
            const fetchHistoriesandFiles = async () => {
                await fetchHistories();
                await fetchUserFiles();
            };
            // Create EventSource connection to listen for updates
            const eventSource = new EventSource('/api/v1/streams'); // Replace with your event URL

            // Event listener to handle updates
            eventSource.onmessage = (event) => {
                console.log('Event received:', event);
                // Fetch histories and files when an event is received
                // Fetch files initially
                fetchHistoriesandFiles();
            };

            // Handle errors
            eventSource.onerror = (error) => {
                console.error('EventSource failed:', error);
                eventSource.close(); // Close the connection if there's an error
            };

            // Cleanup when component is unmounted
            return () => {
                eventSource.close();
            };
        }
    }, [user]); // Runs whenever `user` changes


    const fetchUserFiles = async () => {
        if (!user) {
            return;
        }

        try {
            const token = await user.getIdToken();

            const response = await fetch(`api/v1/files`, {
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            });

            if (response.ok) {
                const files = await response.json();
                setKnowledgeBaseFiles(files);
            } else {
                console.error('Failed to fetch user files:', response.statusText);
            }
        } catch (error) {
            console.error('Error fetching user files:', error);
        }
    }

    const handleDeleteFile = async (fileId: number) => {
        if (!user) {
            console.error('User is not available.');
            return;
        }

        // Add logic to delete the file on the server
        try {
            const token = await user.getIdToken();

            const deleteResponse = await fetch(`api/v1/files/${fileId}`, {
                method: 'DELETE',
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            });

            if (deleteResponse.ok) {
                setKnowledgeBaseFiles((prevFiles) => prevFiles.filter((file) => file.id !== fileId));
            } else {
                console.error('Failed to delete file:', deleteResponse.statusText);
            }
        } catch (error) {
            console.error('Error deleting file:', error);
        }
    };

    return (
        <div className={`chat-app-container ${darkMode ? 'dark-mode' : ''}`}>
            <div className="layout-container">
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
                        <button className="settings-button" onClick={handleSettingsClick}>⚙️</button>
                    )}
                </div>

                {/* Conversation View in the middle */}
                <div className="conversation-column">
                    <ConversationView
                        history={currentHistory !== null ? histories[currentHistory] : null}
                        onCopy={handleCopy}
                    />
                    <InputArea onSend={handleSend} isSending={loading} />
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
            </div>
        </div>
    );


};

export default ChatApp;
