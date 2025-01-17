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
        const type = getFileType(fileUrl);
        if (!type) {
            console.error('Unsupported file type for:', fileUrl);
            onError('Unsupported file type');
            return;
        }

        console.log('Detected file type:', type);
        setFileType(type);
        fetchFileContent(fileUrl);

        // Extract page number from URL
        const page = extractPageNumber(fileUrl);
        if (page) setPageNumber(page);
    }, [fileUrl, onError]);

    const fetchFileContent = async (url: string) => {
        try {
            console.log('Fetching file from:', url);
            const response = await fetch(url, { mode: 'cors' });

            if (response.ok) {
                const blob = await response.blob();
                const dataUrl = URL.createObjectURL(blob);
                setFileContent(dataUrl);
            } else {
                throw new Error(`Failed to fetch file: ${response.statusText}`);
            }
        } catch (error) {
            console.error('Error fetching file content:', error);
            onError('Failed to load file. Please try again later.');
        } finally {
            setLoading(false);
        }
    };

    const getFileType = (url: string): string | null => {
        const extension = url.split('.').pop()?.toLowerCase();
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
            case 'mov':
                return 'video';
            default:
                return null;
        }
    };

    const extractPageNumber = (url: string): number | null => {
        const params = new URLSearchParams(url.split('?')[1]);
        const page = params.get('page');
        return page ? parseInt(page, 10) : null;
    };

    const renderFileContent = () => {
        if (loading) return <p>Loading...</p>;
        if (!fileContent) return <p>Failed to load file. Please try again.</p>;

        switch (fileType) {
            case 'pdf':
                return (
                    <embed
                        src={`${fileContent}#page=${pageNumber}`}
                        type="application/pdf"
                        width="100%"
                        height="750px"
                    />
                );
            case 'image':
                return <img src={fileContent} alt="File content" style={{ width: '100%', height: 'auto' }} />;
            case 'video':
                return (
                    <video controls width="100%" height="auto">
                        <source src={fileContent} type="video/mp4" />
                        Your browser does not support the video tag.
                    </video>
                );
            default:
                return <div>Unsupported file type</div>;
        }
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                {renderFileContent()}
                <button onClick={onClose} aria-label="Close file viewer">
                    ❌
                </button>
            </div>
        </div>
    );
};

export default FileViewerComponent;
