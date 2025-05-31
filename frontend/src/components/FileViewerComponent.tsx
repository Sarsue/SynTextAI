import React, { useState, useEffect, useRef, useCallback } from 'react';
import './FileViewerComponent.css';
import { UploadedFile, KeyConcept } from './types';
import { useUserContext } from '../UserContext';
import { toast } from 'react-toastify';

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
    const [expandedConcepts, setExpandedConcepts] = useState<number[]>([]);
    const [sortOrder, setSortOrder] = useState<string>('default');
    const [highlightedConceptId, setHighlightedConceptId] = useState<number | null>(null);
    
    const pdfViewerRef = useRef<HTMLIFrameElement>(null);
    const videoPlayerRef = useRef<HTMLVideoElement>(null);
    const youtubePlayerRef = useRef<any>(null);

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
        
        console.log(`Fetching key concepts for file ID: ${fileId}, type: ${fileType}, isYouTube: ${fileType === 'youtube'}`);
        
        try {
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');

            console.log(`Making API request to /api/v1/files/${fileId}/key_concepts`);
            const response = await fetch(`/api/v1/files/${fileId}/key_concepts`, {
                method: 'GET',
                headers: { Authorization: `Bearer ${idToken}` },
                mode: 'cors',
            });

            console.log(`Key concepts API response status: ${response.status}`);
            
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.detail || `Failed to fetch key concepts: ${response.statusText}`);
            }

            const responseData = await response.json();
            console.log(`Key concepts API response data:`, responseData);
            
            if (!responseData || !Array.isArray(responseData.key_concepts)) {
                console.error("API response structure incorrect for key concepts:", responseData);
                throw new Error('API returned malformed data structure for key concepts');
            }

            const data: KeyConcept[] = responseData.key_concepts;
            console.log(`Parsed ${data.length} key concepts for file ID: ${fileId}`);
            
            if (data.length === 0) {
                console.warn(`No key concepts found for file ID: ${fileId}, type: ${fileType}`);
            }
            
            setKeyConcepts(data);
        } catch (err) {
            const errorMsg = err instanceof Error ? err.message : 'An unknown error occurred';
            console.error(`Failed to fetch key concepts for file ID: ${fileId}, type: ${fileType}:`, errorMsg);
            setError(errorMsg);
            onError(errorMsg); 
        } finally {
            setIsLoadingKeyConcepts(false);
        }
    }, [user, fileId, fileType, onError]); 


    useEffect(() => {
        // Set file type immediately to avoid null values in logs and UI
        const urlToTest = fileUrl || file.name; 
        const type = getFileType(urlToTest);
        
        console.log(`FileViewerComponent: Input to getFileType: '${urlToTest}'. Detected type: '${type}'. File processed: ${file.processed}`);
        
        // Set file type right away
        setFileType(type);

        if (!type) {
            onError(`Unsupported file type or could not determine type for: ${file.name}`);
        }
        
        // Only fetch key concepts if user is logged in and file ID exists
        if (user && fileId) {
            fetchKeyConcepts();
        }
    }, [fileUrl, file.name, onError, file.processed, user, fileId]); 


    const formatVideoTimestamp = (seconds: number | undefined): string => {
        if (seconds === undefined || seconds === null) return '00:00';
        
        const date = new Date(0);
        date.setSeconds(seconds);
        // For videos longer than an hour, show hours
        return seconds >= 3600 
            ? date.toISOString().substr(11, 8)  // hh:mm:ss
            : date.toISOString().substr(14, 5); // mm:ss
    };

    const expandConcept = (conceptId: number) => {
        setExpandedConcepts(prev => {
            if (prev.includes(conceptId)) {
                return prev.filter(id => id !== conceptId);
            } else {
                return [...prev, conceptId];
            }
        });
    };
    
    const handleSortChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        setSortOrder(e.target.value);
    };

    const sortKeyConcepts = (concepts: KeyConcept[]): KeyConcept[] => {
        switch(sortOrder) {
            case 'alphabetical':
                return [...concepts].sort((a, b) => {
                    return (a.concept_title || '').localeCompare(b.concept_title || '');
                });
            case 'chronological':
                return [...concepts].sort((a, b) => {
                    // First sort by page number
                    const pageComparison = (a.source_page_number || 0) - (b.source_page_number || 0);
                    if (pageComparison !== 0) return pageComparison;
                    
                    // Then by video timestamp if page numbers are the same
                    return (a.source_video_timestamp_start_seconds || 0) - (b.source_video_timestamp_start_seconds || 0);
                });
            default:
                return concepts; // Default order as returned by API
        }
    };

    const handleKeyConceptSourceClick = (concept: KeyConcept) => {
        // Store the currently viewed concept ID for highlighting
        if (concept.id) {
            setHighlightedConceptId(concept.id);
        }
        
        if (concept.source_page_number && pdfViewerRef.current) {
            // For PDFs, navigate to the page number
            const iframe = pdfViewerRef.current;
            iframe.contentWindow?.postMessage({ type: 'goto-page', page: concept.source_page_number }, '*');
            toast.info(`Navigated to page ${concept.source_page_number}`);
        } else if (concept.source_video_timestamp_start_seconds !== null && concept.source_video_timestamp_start_seconds !== undefined) {
            const timestamp = concept.source_video_timestamp_start_seconds;
            
            if (videoPlayerRef.current) {
                // For regular videos
                videoPlayerRef.current.currentTime = timestamp;
                videoPlayerRef.current.play();
                toast.info(`Jumped to ${formatVideoTimestamp(timestamp)}`);
            } else if (youtubePlayerRef.current && fileType === 'youtube') {
                // For YouTube videos
                youtubePlayerRef.current.seekTo(timestamp);
                youtubePlayerRef.current.playVideo();
                toast.info(`Jumped to ${formatVideoTimestamp(timestamp)}`);
            }
        }
        
        // Clear highlight after animation
        setTimeout(() => setHighlightedConceptId(null), 2000);
    };

    const renderFileContent = () => {
        if (!fileType) {
            return <div className="loading-indicator">Loading file...</div>;
        }
        
        // Show loading indicator for unprocessed files
        if (!file.processed) {
            return <div className="processing-indicator">File is being processed. Key concepts will appear when ready.</div>;
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

    // Enhanced SourceLink component for better UI and clearer navigation
    const SourceLink = ({ concept, onNavigate }: { concept: KeyConcept, onNavigate: (concept: KeyConcept) => void }) => {
        const hasPageNumber = concept.source_page_number !== null && concept.source_page_number !== undefined;
        const hasVideoTimestamp = concept.source_video_timestamp_start_seconds !== null && concept.source_video_timestamp_start_seconds !== undefined;
        
        if (!hasPageNumber && !hasVideoTimestamp) {
            return <small>No source location available</small>;
        }
        
        return (
            <div className="source-link-container">
                <small>Found in: </small>
                {hasPageNumber && (
                    <button 
                        onClick={() => onNavigate(concept)} 
                        className="source-link pdf-source"
                    >
                        <span className="source-icon">📄</span>
                        Page {concept.source_page_number}
                    </button>
                )}
                
                {hasVideoTimestamp && concept.source_video_timestamp_start_seconds !== null && (
                    <button 
                        onClick={() => onNavigate(concept)} 
                        className="source-link video-source"
                    >
                        <span className="source-icon">🎬</span>
                        {formatVideoTimestamp(concept.source_video_timestamp_start_seconds)}
                        {concept.source_video_timestamp_end_seconds !== null && 
                            ` - ${formatVideoTimestamp(concept.source_video_timestamp_end_seconds)}`}
                    </button>
                )}
            </div>
        );
    };

    const renderKeyConcepts = () => {
        if (isLoadingKeyConcepts) {
            return <div className="loading-indicator">Loading key concepts...</div>;
        }

        if (error) {
            return <div className="error-message">{error}</div>;
        }

        if (!file.processed) {
            return <div className="processing-message">
                <p>This file is still being processed.</p>
                <p>Key concepts will appear here once processing is complete.</p>
                <div className="processing-spinner"></div>
            </div>;
        }

        if (!keyConcepts || keyConcepts.length === 0) {
            // Add more specific messaging for YouTube videos
            if (fileType === 'youtube') {
                return (
                    <div className="no-key-concepts">
                        <p>No key concepts available for this YouTube video.</p>
                        <p>This may be due to one of the following reasons:</p>
                        <ul>
                            <li>The video transcript could not be properly extracted</li>
                            <li>The video content doesn't contain enough information for key concept generation</li>
                            <li>There was an error during key concept processing</li>
                        </ul>
                        <p><small>Check the browser console for detailed logs</small></p>
                    </div>
                );
            }
            return <div className="no-key-concepts">No key concepts available for this file.</div>;
        }

        // Apply sorting based on user selection
        const sortedConcepts = sortKeyConcepts(keyConcepts);

        return (
            <div className="key-concepts-container">
                <div className="key-concepts-header">
                    <h3>Key Concepts</h3>
                    <div className="key-concepts-controls">
                        <select onChange={handleSortChange} value={sortOrder}>
                            <option value="default">Default Order</option>
                            <option value="alphabetical">Alphabetical</option>
                            <option value="chronological">Source Order</option>
                        </select>
                    </div>
                </div>
                
                <div className="key-concepts-list">
                    {sortedConcepts.map((concept) => (
                        <div 
                            key={concept.id} 
                            className={`key-concept-card ${highlightedConceptId === concept.id ? 'concept-highlight' : ''}`}
                        >
                            <div 
                                className="concept-header"
                                onClick={() => expandConcept(concept.id)}
                            >
                                <h4>{concept.concept_title || 'Untitled Concept'}</h4>
                                <span className="expand-icon">
                                    {expandedConcepts.includes(concept.id) ? "−" : "+"}
                                </span>
                            </div>
                            
                            {expandedConcepts.includes(concept.id) && (
                                <div className="concept-content">
                                    <p>{concept.concept_explanation}</p>
                                    <SourceLink concept={concept} onNavigate={handleKeyConceptSourceClick} />
                                    <small>
                                        <em>Added: {new Date(concept.created_at).toLocaleString()}</em>
                                    </small>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        );
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                <div className="file-viewer-main-layout">
                    <div className="document-view-container">
                        {renderFileContent()} 
                    </div>
                    <div className="key-concepts-panel">
                        {renderKeyConcepts()}
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
