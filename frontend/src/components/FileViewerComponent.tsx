import React, { useState, useEffect } from 'react';
import './FileViewerComponent.css';

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
    const [pageNumber, setPageNumber] = useState<number>(1); // Default page number
    const [videoStartTime, setVideoStartTime] = useState<string | null>(null); // Start time for video
    const [videoEndTime, setVideoEndTime] = useState<string | null>(null); // End time for video

    useEffect(() => {
        const fileType = getFileType(fileUrl);
        if (!fileType) {
            console.error('Unsupported file type for:', fileUrl);
            onError('Unsupported file type');
            return;
        }

        console.log('Detected file type:', fileType);
        setFileType(fileType);

        // Extract page number or video start/end times from URL
        const urlParams = new URLSearchParams(new URL(fileUrl).search);
        const pageParam = urlParams.get('page');
        const startTimeParam = urlParams.get('start_time');
        const endTimeParam = urlParams.get('end_time');

        if (pageParam) {
            setPageNumber(parseInt(pageParam, 10));
        }
        if (startTimeParam) {
            setVideoStartTime(startTimeParam);
        }
        if (endTimeParam) {
            setVideoEndTime(endTimeParam);
        }

        fetchFileContent(fileUrl, fileType);
    }, [fileUrl, onError]);

    const fetchFileContent = async (url: string, type: string) => {
        try {
            const baseUrl = url.split('?')[0]; // Remove query params for fetch URL
            console.log('Fetching file from:', url);

            const response = await fetch(baseUrl, { mode: 'cors' });

            if (response.ok) {
                const blob = await response.blob();
                const dataUrl = URL.createObjectURL(blob);
                setFileContent(dataUrl);
                setLoading(false);
            } else {
                throw new Error('Failed to fetch file content');
            }
        } catch (error) {
            console.error('Error fetching file content:', error);
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
                return 'image'; // Handle common image formats
            case 'mp4':
            case 'mkv':
            case 'avi':
            case 'mov':
            case 'wmv':
            case 'flv':
            case 'webm':
            case 'mpeg':
            case 'mpg':
            case '3gp':
                return 'video'; // Handle common video formats
            default:
                return null; // Unsupported file type
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
                    <video controls width="100%" height="auto" currentTime={parseTime(videoStartTime || '0')}>
                        <source src={fileContent!} type="video/mp4" />
                        Your browser does not support the video tag or the video format.
                    </video>
                );
            default:
                return <div>Unsupported file type</div>; // Handle unsupported types
        }
    };

    // Helper function to convert start time to seconds for the video player
    const parseTime = (timeStr: string): number => {
        const [hours, minutes, seconds] = timeStr.split(':').map(Number);
        return hours * 3600 + minutes * 60 + seconds;
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                {renderFileContent()}
                <button onClick={onClose}>❌</button>
            </div>
        </div>
    );
};

export default FileViewerComponent;
