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
        const fetchFileContent = async () => {
            try {
                const baseUrl = fileUrl.split('?')[0];
                console.log('Fetching file from:', baseUrl);

                const response = await fetch(baseUrl, { mode: 'cors' });

                if (response.ok) {
                    const blob = await response.blob();
                    const dataUrl = URL.createObjectURL(blob);
                    setFileContent(dataUrl);
                    setLoading(false);
                    // Determine file type based on URL or response headers
                    const fileType = getFileType(baseUrl);
                    setFileType(fileType);

                    // Extract page number from URL if provided
                    const match = fileUrl.match(/[?&]page=(\d+)/);
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

        fetchFileContent();
    }, [fileUrl, onError]);

    const getFileType = (fileUrl: string): string | null => {
        const extension = fileUrl.split('.').pop()?.toLowerCase();
        switch (extension) {
            case 'pdf':
                return 'pdf';
            case 'mp4':
                return 'mp4';
            case 'txt':
                return 'txt';
            case 'docx':
                return 'docx';
            default:
                return null;
        }
    };

    const renderFileContent = () => {
        if (loading) return <p>Loading...</p>;

        switch (fileType) {
            case 'pdf':
                return (
                    <div style={{ height: '750px' }}>
                        <embed src={`${fileContent}#page=${pageNumber}`} type="application/pdf" width="100%" height="100%" />
                        <div>
                            {pageNumber > 1 && <button onClick={() => setPageNumber(pageNumber - 1)}>Previous</button>}
                            <button onClick={() => setPageNumber(pageNumber + 1)}>Next</button>
                        </div>
                    </div>
                );
            case 'mp4':
                return <video src={fileContent!} controls width="100%" />;
            case 'txt':
                return <pre>{fileContent}</pre>;
            case 'docx':
                return <div>Render DOCX here</div>;
            default:
                return <div>{fileContent}</div>;
        }
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                {renderFileContent()}
                <button onClick={onClose}>Close</button>
            </div>
        </div>
    );
};

export default FileViewerComponent;
