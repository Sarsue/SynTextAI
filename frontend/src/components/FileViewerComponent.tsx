import React, { useState, useEffect } from 'react';
import './FileViewerComponent.css';

interface FileViewerComponentProps {
    fileUrl: string;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}

const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ fileUrl, onClose, onError, darkMode }) => {
    const [fileType, setFileType] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [pageNumber, setPageNumber] = useState<number | null>(null);
    const [videoStartTime, setVideoStartTime] = useState<number | null>(null);

    useEffect(() => {
        // Extract page or time parameters if they exist
        try {
            const url = new URL(fileUrl);
            const pageParam = url.searchParams.get('page');
            if (pageParam) {
                setPageNumber(parseInt(pageParam, 10));
            }
            
            const timeParam = url.searchParams.get('time');
            if (timeParam) {
                setVideoStartTime(parseInt(timeParam, 10));
            }
        } catch (e) {
            console.log('Could not parse URL parameters');
        }
        
        const detectedFileType = getFileType(fileUrl);
        if (!detectedFileType) {
            console.log(`Unsupported file type for: ${fileUrl}`);
            onError('Unsupported file type');
            setLoading(false);
            return;
        }

        setFileType(detectedFileType);
        setLoading(false); // No fetching needed, we're done loading
    }, [fileUrl, onError]);

    // Re-add getFileType function (assuming it was removed)
    const getFileType = (fileUrl: string): string | null => {
        try {
            const urlObj = new URL(fileUrl.split('?')[0]); // Use only base URL for extension detection
            const pathname = urlObj.pathname;
            const extension = pathname.split('.').pop()?.toLowerCase();

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
        } catch (error) {
            console.error("Error parsing URL in getFileType:", fileUrl, error);
            return null;
        }
    };

    // Ensure renderFileContent uses fileContent
    const renderFileContent = () => {
        if (loading) {
            return <div className="loading-indicator">Loading...</div>;
        }

        if (!fileType) {
            return <div className="error-message">Could not determine file type</div>;
        }

        switch (fileType) {
            case 'pdf':
                // Use a direct iframe approach with parameters to force viewing
                // The #view=FitH parameter helps trigger the browser's PDF viewer
                const pdfUrlWithViewParam = `${fileUrl}#view=FitH`;
                return (
                    <div className="pdf-container">
                        <iframe
                            src={pdfUrlWithViewParam}
                            width="100%"
                            height="750px"
                            style={{ border: 'none' }}
                            title="PDF Viewer"
                        ></iframe>
                        <div className="pdf-fallback-links">
                            <a 
                                href={fileUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="open-pdf-link"
                            >
                                Open in new tab
                            </a>
                        </div>
                    </div>
                );
            case 'image':
                return <img src={fileUrl} alt="File content" style={{ width: '100%', height: 'auto' }} />;
            case 'video':
                return (
                    <video 
                        controls 
                        width="100%" 
                        height="auto"
                        {...(videoStartTime ? { currentTime: videoStartTime } : {})}
                    >
                        <source src={fileUrl} type="video/mp4" />
                        Your browser does not support the video tag or the video format.
                    </video>
                );
            default:
                return <div>Unsupported file type</div>;
        }
    };

    const handleClose = () => {
        onClose();
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                {renderFileContent()}
                {/* Consider making the close button more accessible */}
                <button onClick={handleClose} className="close-button" aria-label="Close file viewer">❌</button>
            </div>
        </div>
    );
};

export default FileViewerComponent;
