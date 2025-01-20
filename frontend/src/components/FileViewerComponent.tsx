import React, { useState, useEffect } from 'react';
import './FileViewerComponent.css';
import { LogUIActions } from '../apiUtils';

interface FileViewerComponentProps {
    fileUrl: string;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}

const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ fileUrl, onClose, onError, darkMode }) => {
    const [fileContent, setFileContent] = useState<string | null>(null);
    const [fileType, setFileType] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [pageNumber, setPageNumber] = useState<number | null>(null);
    const [videoStartTime, setVideoStartTime] = useState<number | null>(null);

    useEffect(() => {
        const fileType = getFileType(fileUrl);
        if (!fileType) {
            const message = `Unsupported file type for: ${fileUrl}`;
            LogUIActions('api/v1/logs', 'POST', `User attempted to view unsupported file type: ${message}`, 'error');
            console.log(message);
            onError('Unsupported file type');
            return;
        }

        setFileType(fileType);
        fetchFileContent(fileUrl, fileType);
    }, [fileUrl, onError]);

    const fetchFileContent = async (url: string, type: string) => {
        try {
            const baseUrl = url.split('?')[0]; // Ensure to remove query params for fetching the file
            const response = await fetch(baseUrl, { mode: 'cors' });

            if (response.ok) {
                const blob = await response.blob();
                const dataUrl = URL.createObjectURL(blob);
                setFileContent(dataUrl);
                setLoading(false);

                // Handle query params for page number or video start time
                const urlParams = new URLSearchParams(url.split('?')[1]);
                if (urlParams.has('page')) {
                    const pageNum = parseInt(urlParams.get('page') || '1', 10);
                    setPageNumber(pageNum);
                }
                if (urlParams.has('time')) {
                    const time = parseInt(urlParams.get('time') || '0', 10);
                    setVideoStartTime(time);
                }

                LogUIActions('api/v1/logs', 'POST', `File successfully loaded: ${baseUrl}`, 'info');
            } else {
                throw new Error('Failed to fetch file content');
            }
        } catch (error) {
            console.error('Error fetching file content:', error);
            LogUIActions('api/v1/logs', 'POST', `Error loading file: ${url}. Error: ${error}`, 'error');
            onError('Failed to load file. Please try again later.');
            setLoading(false);
        }
    };

    const getFileType = (fileUrl: string): string | null => {
        const extension = fileUrl.split('.').pop()?.toLowerCase();
        switch (extension) {
            case 'pdf':
                return 'pdf';
            case 'jpg':
            case 'jpeg':
            case 'png':
            case 'gif':
                return 'image';
            case 'mp4':
            case 'mkv':
            case 'avi':
                return 'video';
            default:
                return null;
        }
    };

    const renderFileContent = () => {
        if (loading) return <p>Loading...</p>;

        switch (fileType) {
            case 'pdf':
                return (
                    <embed src={`${fileContent}#page=${pageNumber}`} type="application/pdf" width="100%" height="750px" />
                );
            case 'image':
                return <img src={fileContent!} alt="File content" style={{ width: '100%', height: 'auto' }} />;
            case 'video':
                return (
                    <video controls width="100%" height="auto">
                        <source src={fileContent!} type="video/mp4" />
                        Your browser does not support the video tag or the video format.
                    </video>
                );
            default:
                return <div>Unsupported file type</div>;
        }
    };

    const handleClose = () => {
        LogUIActions('api/v1/logs', 'POST', `User closed file viewer for: ${fileUrl}`, 'info');
        onClose();
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                {renderFileContent()}
                <button onClick={handleClose}>❌</button>
            </div>
        </div>
    );
};

export default FileViewerComponent;
