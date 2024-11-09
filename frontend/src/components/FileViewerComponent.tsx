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

    useEffect(() => {
        const fileType = getFileType(fileUrl);
        if (!fileType) {
            onError('Unsupported file type');
            return; // Stop processing if the file type is not supported
        }

        setFileType(fileType);
        fetchFileContent(fileUrl, fileType);
    }, [fileUrl, onError]);

    const fetchFileContent = async (url: string, type: string) => {
        try {
            const baseUrl = url.split('?')[0];
            console.log('Fetching file from:', baseUrl);

            const response = await fetch(baseUrl, { mode: 'cors' });

            if (response.ok) {
                const blob = await response.blob();
                const dataUrl = URL.createObjectURL(blob);
                setFileContent(dataUrl);
                setLoading(false);

                // Extract page number from URL if provided
                const match = url.match(/[?&]page=(\d+)/);
                if (match) {
                    const pageNum = parseInt(match[1], 10);
                    setPageNumber(pageNum);
                }
            } else {
                throw new Error('Failed to fetch file content');
            }
        } catch (error) {
            onError(error.message);
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
                    <video controls width="100%" height="auto">
                        <source src={fileContent!} type="video/mp4" />
                        Your browser does not support the video tag.
                    </video>
                );
            default:
                return <div>Unsupported file type</div>; // Handle unsupported types
        }
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
