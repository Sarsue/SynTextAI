import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useUserContext } from '../UserContext';
import './FileViewerComponent.css';
import { UploadedFile, KeyConcept } from './types';
// import SelectionToolbox from './SelectionToolbox'; // SelectionToolbox removed

interface FileViewerComponentProps {
    file: UploadedFile;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}


const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ file, onClose, onError, darkMode }) => {
    const { user } = useUserContext();
    const [fileType, setFileType] = useState<string | null>(null);
    const [currentPage, setCurrentPage] = useState<number>(1); // Keep for PDF page tracking if needed
    const [keyConcepts, setKeyConcepts] = useState<KeyConcept[]>([]);
    const [isLoadingKeyConcepts, setIsLoadingKeyConcepts] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);
    // Removed states: showExplainPanel, isExplaining, explanationHistory, isLoadingHistory, selectionToolboxPosition, selectionText, isExplanationLoading, explanation, allExplanations
    // Removed: isPremiumUser, showTools, debugSelection unless they are used for other purposes not evident here.

    const pdfViewerRef = useRef<HTMLIFrameElement>(null);
    const videoPlayerRef = useRef<HTMLVideoElement>(null);

    const fileId = file.id;
    const fileUrl = file.publicUrl;

    const getFileType = (urlOrName: string): string | null => {
        // Check for YouTube URLs first using a regex
        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com\/(watch\?v=|embed\/|shorts\/)|youtu\.be\/)/i;
        if (youtubeRegex.test(urlOrName)) {
            return 'youtube';
        }

        // Original extension-based check
        const extension = urlOrName.split('.').pop()?.toLowerCase();
        if (!extension) {
            return null; // No extension and not identified as YouTube
        }

        if (extension === 'pdf') return 'pdf';
        if (['mp4', 'webm', 'ogg', 'mov'].includes(extension)) return 'video';
        if (['jpg', 'jpeg', 'png', 'gif'].includes(extension)) return 'image';
        
        return null; 
    };

    const fetchKeyConcepts = useCallback(async () => {
        if (!user || !fileId) return;
        setIsLoadingKeyConcepts(true);
        setError(null);
        try {
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');

            const response = await fetch(`/api/v1/files/${fileId}/key_concepts`, {
                method: 'GET',
                headers: { Authorization: `Bearer ${idToken}` },
                mode: 'cors',
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.detail || `Failed to fetch key concepts: ${response.statusText}`);
            }

            const responseData = await response.json();
            if (!responseData || !Array.isArray(responseData.key_concepts)) {
                console.error("API response structure incorrect for key concepts:", responseData);
                throw new Error('API returned malformed data structure for key concepts');
            }

            const data: KeyConcept[] = responseData.key_concepts;
            setKeyConcepts(data);
        } catch (err) {
            const errorMsg = err instanceof Error ? err.message : 'An unknown error occurred';
            console.error('Failed to fetch key concepts:', errorMsg);
            setError(errorMsg);
            onError(errorMsg); 
        } finally {
            setIsLoadingKeyConcepts(false);
        }
    }, [user, fileId, onError]); 


    useEffect(() => {
        const urlToTest = fileUrl || file.name; 
        const type = getFileType(urlToTest);
        
        console.log(`FileViewerComponent: Input to getFileType: '${urlToTest}'. Detected type: '${type}'.`);
        setFileType(type);

        if (!type) {
            onError(`Unsupported file type or could not determine type for: ${file.name}`);
        }
    }, [fileUrl, file.name, onError]); 
    
    useEffect(() => {
        const type = getFileType(fileUrl);
        setFileType(type);
        fetchKeyConcepts(); 
    }, [fileUrl, fetchKeyConcepts]); 


    const handleKeyConceptSourceClick = (concept: KeyConcept) => {
        if (fileType === 'pdf' && concept.source_page_number && pdfViewerRef.current) {
            console.log(`Navigate to PDF page: ${concept.source_page_number}`);
        } else if ((fileType === 'youtube' || fileType === 'video') && concept.source_video_timestamp_start_seconds && videoPlayerRef.current) {
            videoPlayerRef.current.currentTime = concept.source_video_timestamp_start_seconds;
            videoPlayerRef.current.play();
        }
    };

    const renderFileContent = () => {
        if (!fileType) {
            return <div className="loading-indicator">Loading file...</div>;
        }

        if (fileType === 'youtube') { 
            let videoId = '';
            const url = file.publicUrl || ''; 
            const match = url.match(/(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|embed)\/|.*[?\&]v=)|youtu\.be\/|youtube\.com\/shorts\/)([\w-]{11})/);
            if (match && match[1]) {
                videoId = match[1];
            }
            return (
                <div className="youtube-container file-content-area">
                    <iframe
                        className="youtube-iframe" 
                        width="100%"
                        src={`https://www.youtube.com/embed/${videoId}`}
                        title="YouTube video player"
                        frameBorder="0"
                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                        allowFullScreen
                    />
                </div>
            );
        }

        switch (fileType) {
            case 'pdf':
                return (
                    <div className="pdf-container file-content-area">
                        <iframe
                            ref={pdfViewerRef}
                            src={fileUrl}
                            className="pdf-viewer"
                            title={`PDF Viewer - ${file.name}`}
                        />
                    </div>
                );
            case 'video':
                return (
                    <div className="video-container file-content-area">
                        <video
                            ref={videoPlayerRef}
                            src={fileUrl}
                            className="video-player"
                            controls
                            onTimeUpdate={() => { /* Can potentially update state here if needed */ }}
                        >
                            Your browser does not support the video tag.
                        </video>
                    </div>
                );
            case 'image':
                return (
                    <div className="image-container file-content-area">
                        <img src={fileUrl} alt={file.name} className="image-viewer" />
                    </div>
                );
            default:
                return <div className="error-message">Unsupported file type: {fileType}</div>;
        }
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                <div className="file-viewer-main-layout">
                    <div className="document-view-container">
                        {renderFileContent()} 
                    </div>
                    <div className="key-concepts-panel">
                        <h3>Key Concepts</h3>
                        {isLoadingKeyConcepts && <p>Loading key concepts...</p>}
                        {error && <p className="error-message">{error}</p>}
                        {!isLoadingKeyConcepts && keyConcepts.length === 0 && !error && <p>No key concepts extracted for this file.</p>}
                        {keyConcepts.length > 0 && (
                            <ul className="key-concepts-list">
                                {keyConcepts.map((concept) => (
                                    <li key={concept.id} className="key-concept-item">
                                        <h4>{concept.concept_title || 'Untitled Concept'}</h4>
                                        <p>{concept.concept_explanation}</p>
                                        <small>
                                            Source: 
                                            {concept.source_page_number && (
                                                <button onClick={() => handleKeyConceptSourceClick(concept)} className="source-link">
                                                    Page {concept.source_page_number}
                                                </button>
                                            )}
                                            {(concept.source_video_timestamp_start_seconds !== null && concept.source_video_timestamp_start_seconds !== undefined) && (
                                                <button onClick={() => handleKeyConceptSourceClick(concept)} className="source-link">
                                                    Time: {new Date(concept.source_video_timestamp_start_seconds * 1000).toISOString().substr(14, 5)}
                                                    {concept.source_video_timestamp_end_seconds && 
                                                        ` - ${new Date(concept.source_video_timestamp_end_seconds * 1000).toISOString().substr(14, 5)}`}
                                                </button>
                                            )}
                                            {!concept.source_page_number && (concept.source_video_timestamp_start_seconds === null || concept.source_video_timestamp_start_seconds === undefined) && <span>N/A</span>}
                                            <br />
                                            <em>Added: {new Date(concept.created_at).toLocaleString()}</em>
                                        </small>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </div>
                <div className="viewer-controls-bottom">
                    <button
                        onClick={onClose}
                        className="toolbar-button close-button-bottom"
                        title="Close Viewer"
                        aria-label="Close file viewer"
                    >
                        Close Viewer
                    </button>
                </div>
            </div>
            {/* SelectionToolbox removed as its primary function (explain selection) is gone */}
        </div>
    );
}

export default FileViewerComponent;
