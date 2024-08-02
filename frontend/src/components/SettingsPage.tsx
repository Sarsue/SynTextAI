// SettingsPage.tsx
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import PaymentView from './PaymentView';
import KnowledgeBaseComponent from './KnowledgeBaseComponent';
import DarkModeToggle from './DarkModeToggle';
import LanguageToggle from './LanguageToggle';
import FileViewerComponent from './FileViewerComponent';
import { loadStripe, Stripe } from '@stripe/stripe-js';
import './SettingsPage.css'; // Import the CSS file
import { User } from 'firebase/auth';
import { useDarkMode } from '../DarkModeContext';
import { Persona } from './types';

interface File {
    id: number;
    name: string;
    publicUrl: string;
}

interface SettingsPageProps {
    stripePromise: Promise<Stripe | null>;
    user: User | null; // Adjust the user prop type
    subscriptionStatus: string | null;
}

const SettingsPage: React.FC<SettingsPageProps> = ({ stripePromise, user, subscriptionStatus }) => {
    const navigate = useNavigate();
    const [activeTab, setActiveTab] = useState<'payment' | 'knowledge' | 'general'>('general');
    const [knowledgeBaseFiles, setKnowledgeBaseFiles] = useState<File[]>([]);
    const [selectedFile, setSelectedFile] = useState<File | null>(null); // State for selected file
    const [subscriptionStatusLocal, setSubscriptionStatusLocal] = useState<string | null>(subscriptionStatus);
    const { darkMode, setDarkMode } = useDarkMode();
    const [saveMessage, setSaveMessage] = useState<string>(''); // State for save message
    const [multilingual, setMultilingual] = useState(false);
    const toggleDarkMode = () => {
        setDarkMode(!darkMode);
    };
    const handleSubscriptionChange = (newStatus: string) => {
        setSubscriptionStatusLocal(newStatus);
    };
    const handleFileClick = (file: File) => {
        setSelectedFile(file); // Set the selected file when clicked
    };
    const handleCloseFileViewer = () => {
        setSelectedFile(null); // Clear selected file when closing viewer
    };

    useEffect(() => {
        // Fetch user files and personas with the user token whenever user changes
        if (user) {
            fetchUserFiles();
        }
    }, [user]); // Run this effect whenever the user object changes

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
    };

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

    const handleFileError = (error: string) => {
        setSelectedFile(null);
    };

    return (
        <div className={`settings-container ${darkMode ? 'dark-mode' : ''}`}>
            <button className="close-button" onClick={() => navigate('/chat')}>
                ❌
            </button>
            <div className={`tab-buttons ${darkMode ? 'dark-mode' : ''}`}>
                <button className={activeTab === 'general' ? 'active' : ''} onClick={() => setActiveTab('general')}>General</button>
                {/* <button className={activeTab === 'payment' ? 'active' : ''} onClick={() => setActiveTab('payment')}>Payment</button> */}
                <button className={activeTab === 'knowledge' ? 'active' : ''} onClick={() => setActiveTab('knowledge')}>Knowledge Management</button>
            </div>
            <div className={`settings-content ${darkMode ? 'dark-mode' : ''}`}>
                {activeTab === 'knowledge' && (
                    <>
                        <KnowledgeBaseComponent
                            files={knowledgeBaseFiles}
                            onDeleteFile={handleDeleteFile}
                            onFileClick={handleFileClick} // Pass handleFileClick as onFileClick prop
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
                    </>
                )}
                {/* {activeTab === 'payment' && (
                    <PaymentView
                        stripePromise={stripePromise} user={user}
                        subscriptionStatus={subscriptionStatusLocal}
                        onSubscriptionChange={handleSubscriptionChange}
                        darkMode={darkMode} />

                )} */}
                {activeTab === 'general' && (
                    <>
                        <DarkModeToggle darkMode={darkMode} setDarkMode={setDarkMode} />
                        {/* <LanguageToggle multilingual={multilingual} setMultilingual={setMultilingual} /> */}
                    </>
                )}
            </div>
        </div>
    );

};

export default SettingsPage;