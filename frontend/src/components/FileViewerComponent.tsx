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
    const [pageNumber, setPageNumber] = useState<number | null>(null);
    const [videoStartTime, setVideoStartTime] = useState<number | null>(null);

    useEffect(() => {
        const detectedFileType = getFileType(fileUrl);
        if (!detectedFileType) {
            console.log(`Unsupported file type for: ${fileUrl}`);
            onError('Unsupported file type');
            return;
        }

        setFileType(detectedFileType);
        extractQueryParams(fileUrl);
    }, [fileUrl, onError]);

    const getFileType = (fileUrl: string): string | null => {
        const url = new URL(fileUrl);
        const extension = url.pathname.split('.').pop()?.toLowerCase();

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

    const extractQueryParams = (url: string) => {
        const params = new URL(url).searchParams;
        if (params.has('page')) {
            setPageNumber(parseInt(params.get('page') || '1', 10));
        }
        if (params.has('time')) {
            setVideoStartTime(parseInt(params.get('time') || '0', 10));
        }
    };

    const renderFileContent = () => {
        switch (fileType) {
            case 'pdf':
                return (
                    <embed
                        src={`${fileUrl}#page=${pageNumber || 1}`}
                        type="application/pdf"
                        width="100%"
                        height="750px"
                    />
                );
            case 'image':
                return <img src={fileUrl} alt="File content" style={{ width: '100%', height: 'auto' }} />;
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
                        <source src={fileUrl} type="video/mp4" />
                        Your browser does not support the video tag or the video format.
                    </video>
                );
            case 'webpage':
                return (
                    <iframe src={fileUrl} width="100%" height="750px" style={{ border: 'none' }} title="Web Page Viewer"></iframe>
                );
            default:
                return <div>Unsupported file type</div>;
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
