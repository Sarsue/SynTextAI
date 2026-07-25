import React, { useState, useEffect, useRef } from 'react';
import './FileViewerComponent.css';
import { UploadedFile, ProcessingStatus } from './types';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface FileViewerComponentProps {
    file: UploadedFile;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}

const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ file, onClose, onError, darkMode }) => {
    const [fileType, setFileType] = useState<string>('unknown');
    const [pdfIframeSrc, setPdfIframeSrc] = useState<string>(file.file_url);
    const [youtubeIframeSrc, setYoutubeIframeSrc] = useState<string>('');

    const pdfViewerRef = useRef<HTMLIFrameElement>(null);
    const videoPlayerRef = useRef<HTMLVideoElement>(null);

    const fileUrl = file.file_url;

    useEffect(() => {
        setPdfIframeSrc(fileUrl);
        setYoutubeIframeSrc('');
    }, [fileUrl]);

    const getFileType = (urlOrName: string): string | null => {
        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com\/(watch\?v=|embed\/|shorts\/)|youtu\.be\/)/i;
        if (youtubeRegex.test(urlOrName)) {
            return 'youtube';
        }

        const extension = urlOrName.split('.').pop()?.toLowerCase();
        if (!extension) {
            return null;
        }

        if (extension === 'pdf') return 'pdf';
        if (['mp4', 'webm', 'ogg', 'mov'].includes(extension)) return 'video';
        if (['jpg', 'jpeg', 'png', 'gif'].includes(extension)) return 'image';

        return null;
    };

    const STATUS_MESSAGES: Record<ProcessingStatus, string> = {
        uploaded: 'File has been uploaded and is queued for processing.',
        extracting: 'Extracting content from the file...',
        embedding: 'Generating embeddings for semantic search...',
        storing: 'Storing extracted content and metadata...',
        generating_concepts: 'Processing...',
        processed: 'Processed successfully.',
        failed: 'File processing failed. Please try again later.'
    };

    useEffect(() => {
        const urlToTest = fileUrl || file.file_name;
        const type = getFileType(urlToTest) || 'unknown';
        setFileType(type);

        if (type === 'unknown' && (fileUrl || file.file_name)) {
            onError(`Unsupported file type or could not determine type for: ${file.file_name}`);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fileUrl, file.file_name]);

    const renderFileContent = () => {
        if (!fileType) {
            return <div className="loading-indicator">Loading file...</div>;
        }

        if (file.status !== 'processed') {
            const statusMessage = STATUS_MESSAGES[file.status] || 'File is being processed...';
            return <div className="processing-indicator">{statusMessage}</div>;
        }

        if (fileType === 'youtube') {
            let videoId = '';
            const url = file.file_url || '';
            const match = url.match(/(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|embed)\/|.*[?\&]v=)|youtu\.be\/|youtube\.com\/shorts\/)([\w-]{11})/);
            if (match && match[1]) {
                videoId = match[1];
            }
            return (
                <div className="youtube-container file-content-area">
                    <iframe
                        className="youtube-iframe"
                        width="100%"
                        src={youtubeIframeSrc || `https://www.youtube.com/embed/${videoId}`}
                        title="YouTube video player"
                        frameBorder="0"
                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                        allowFullScreen
                    />
                </div>
            );
        } else if (fileType === 'pdf') {
            return (
                <div className="pdf-container file-content-area">
                    <iframe
                        key={pdfIframeSrc}
                        ref={pdfViewerRef}
                        src={pdfIframeSrc}
                        className="pdf-viewer"
                        title={`PDF Viewer - ${file.file_name}`}
                    />
                </div>
            );
        } else if (fileType === 'video') {
            return (
                <div className="video-container file-content-area">
                    <video
                        ref={videoPlayerRef}
                        src={fileUrl}
                        className="video-player"
                        controls
                    >
                        Your browser does not support the video tag.
                    </video>
                </div>
            );
        } else if (fileType === 'image') {
            return (
                <div className="image-container file-content-area">
                    <img src={fileUrl} alt={file.file_name} className="image-viewer" />
                </div>
            );
        } else {
            return <div className="error-message">Unsupported file type: {fileType}</div>;
        }
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                <Button
                    variant="ghost"
                    size="icon-sm"
                    className="close-button"
                    onClick={onClose}
                >
                    <X className="size-4" />
                </Button>

                <div className="file-viewer-header">
                    <h2>{file.file_name}</h2>
                </div>

                <div className="file-viewer-main-layout">
                    <div className="document-view-container">
                        {renderFileContent()}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default FileViewerComponent;
