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
        const detectedFileType = getFileType(fileUrl);
        if (!detectedFileType) {
            const message = `Unsupported file type for: ${fileUrl}`;
            LogUIActions('api/v1/logs', 'POST', `User attempted to view unsupported file type: ${message}`, 'error');
            console.log(message);
            onError('Unsupported file type');
            return;
        }

        setFileType(detectedFileType);

        // If it's a webpage, no need to fetch it, just use the URL directly
        if (detectedFileType === 'webpage') {
            setFileContent(fileUrl);
            setLoading(false);
        } else {
            fetchFileContent(fileUrl, detectedFileType);
        }
    }, [fileUrl, onError]);

    const fetchFileContent = async (url: string, type: string) => {
        try {
            const [baseUrl, queryParams] = url.split('?');
            const response = await fetch(baseUrl, { mode: 'cors' });

            if (response.ok) {
                const blob = await response.blob();
                const dataUrl = URL.createObjectURL(blob);
                setFileContent(dataUrl);
                setLoading(false);

                const params = new URLSearchParams(queryParams);
                if (params.has('page')) {
                    const pageNum = parseInt(params.get('page') || '1', 10);
                    setPageNumber(pageNum);
                }
                if (params.has('time')) {
                    const time = parseInt(params.get('time') || '0', 10);
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
        const url = new URL(fileUrl);
        const pathname = url.pathname;
        const extension = pathname.split('.').pop()?.toLowerCase();

        if ((fileUrl.startsWith('http://') || fileUrl.startsWith('https://')) && !extension) {

            return 'webpage'; // Detect web page links
        }

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
            case 'html':
                return 'webpage';
            default:
                return null;
        }
    };

    const renderFileContent = () => {
        if (loading) return <p>Loading...</p>;

        switch (fileType) {
            case 'pdf':
                return (
                    <embed
                        src={`${fileContent}#page=${pageNumber || 1}`}
                        type="application/pdf"
                        width="100%"
                        height="750px"
                    />
                );
            case 'image':
                return <img src={fileContent!} alt="File content" style={{ width: '100%', height: 'auto' }} />;
            case 'video':
                return (
                    <video
                        controls
                        width="100%"
                        height="auto"
                        autoPlay={!!videoStartTime}
                        onLoadedMetadata={(e: any) => {
                            if (videoStartTime) e.target.currentTime = videoStartTime;
                        }}
                    >
                        <source src={fileContent!} type="video/mp4" />
                        Your browser does not support the video tag or the video format.
                    </video>
                );
            case 'webpage':
                return (
                    <iframe src={fileContent!} width="100%" height="750px" style={{ border: 'none' }} title="Web Page Viewer"></iframe>
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
