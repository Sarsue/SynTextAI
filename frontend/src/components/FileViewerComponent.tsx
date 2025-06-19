import React, { useState, useEffect, useRef, useCallback } from 'react';
import './FileViewerComponent.css';
import { UploadedFile, KeyConcept, Flashcard, QuizQuestion } from './types';
import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';

import FlashcardViewer from './FlashcardViewer';
import QuizInterface from './QuizInterface';

interface FileViewerComponentProps {
    file: UploadedFile;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}

interface SourceLinkProps {
    concept: KeyConcept;
    onNavigate: (conceptId: number, sourcePage?: number, videoStartTimestamp?: number) => void;
}

const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ file, onClose, onError, darkMode }) => {
    const { user } = useUserContext();
    const { addToast } = useToast();
    const isMountedRef = useRef(true);
    const highlightTimeoutRef = useRef<NodeJS.Timeout>();
    const [fileType, setFileType] = useState<string>('unknown');
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [keyConcepts, setKeyConcepts] = useState<KeyConcept[]>([]);
    const [isLoadingKeyConcepts, setIsLoadingKeyConcepts] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [expandedConcepts, setExpandedConcepts] = useState<Set<number>>(new Set());
    const [highlightedConceptId, setHighlightedConceptId] = useState<number | null>(null);
    const [sortOrder, setSortOrder] = useState<'default' | 'alphabetical' | 'chronological'>('default');
    
    // Custom key concepts state
    const [customKeyConcepts, setCustomKeyConcepts] = useState<KeyConcept[]>([]);
    const [showKeyConceptForm, setShowKeyConceptForm] = useState(false);
    const [newConceptTitle, setNewConceptTitle] = useState('');
    const [newConceptExplanation, setNewConceptExplanation] = useState('');
    const [newConceptSourcePage, setNewConceptSourcePage] = useState<number | null>(null);
    const [newConceptVideoStart, setNewConceptVideoStart] = useState<number | null>(null);
    const [newConceptVideoEnd, setNewConceptVideoEnd] = useState<number | null>(null);
    const [tab, setTab] = useState<'Key Concepts' | 'Flashcards' | 'Quiz'>('Key Concepts');
    const [flashcards, setFlashcards] = useState<Flashcard[]>([]);
    const [quizzes, setQuizzes] = useState<QuizQuestion[]>([]);
    const [isLoadingFlashcards, setIsLoadingFlashcards] = useState(false);
    const [isLoadingQuizzes, setIsLoadingQuizzes] = useState(false);
    const [flashcardError, setFlashcardError] = useState<string | null>(null);
    const [quizError, setQuizError] = useState<string | null>(null);
    
    // State for custom flashcard and quiz creation
    
    // State for custom flashcard creation
    const [customFlashcards, setCustomFlashcards] = useState<Flashcard[]>([]);
    const [newFlashcardQuestion, setNewFlashcardQuestion] = useState('');
    const [newFlashcardAnswer, setNewFlashcardAnswer] = useState('');
    const [showFlashcardForm, setShowFlashcardForm] = useState(false);
    
    // State for custom quiz creation
    const [customQuizzes, setCustomQuizzes] = useState<QuizQuestion[]>([]);
    const [newQuizQuestion, setNewQuizQuestion] = useState('');
    const [newQuizAnswer, setNewQuizAnswer] = useState('');
    const [newQuizType, setNewQuizType] = useState<'MCQ' | 'TF'>('MCQ');
    const [newQuizDistractors, setNewQuizDistractors] = useState('');    
    const [showQuizForm, setShowQuizForm] = useState(false);

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
            
            // Handle the standardized response format
            if (!responseData || responseData.status !== 'success' || !responseData.data || !Array.isArray(responseData.data.key_concepts)) {
                console.error("API response structure incorrect for key concepts:", responseData);
                const errorMessage = responseData.message || 'API returned malformed data structure for key concepts';
                throw new Error(errorMessage);
            }

            const data: KeyConcept[] = responseData.data.key_concepts;
            console.log(`Parsed ${data.length} key concepts for file ID: ${fileId} (total count: ${responseData.count})`);
            
            // Debug logging to inspect the structure of key concepts
            if (data.length > 0) {
                console.log('First key concept complete structure:', data[0]);
                console.log('Key concept field values check:', {
                    hasConceptTitle: data.some(kc => kc.concept_title && kc.concept_title.length > 0),
                    conceptTitles: data.map(kc => kc.concept_title),
                    hasConceptExplanation: data.some(kc => kc.concept_explanation && kc.concept_explanation.length > 0),
                    conceptExplanations: data.map(kc => kc.concept_explanation?.substring(0, 20))
                });
            }
            
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

    const fetchFlashcards = useCallback(async () => {
        if (!user || !fileId) return;
        setIsLoadingFlashcards(true);
        setFlashcardError(null);
        
        try {
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');

            console.log(`Making API request to /api/v1/files/${fileId}/flashcards`);
            const response = await fetch(`/api/v1/files/${fileId}/flashcards`, {
                method: 'GET',
                headers: { Authorization: `Bearer ${idToken}` },
                mode: 'cors',
            });

            console.log(`Flashcards API response status: ${response.status}`);
            
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.detail || `Failed to fetch flashcards: ${response.statusText}`);
            }

            const responseData = await response.json();
            console.log(`Flashcards API response data:`, responseData);
            
            // Handle the standardized response format
            if (!responseData || responseData.status !== 'success' || !responseData.data || !Array.isArray(responseData.data.flashcards)) {
                console.error("API response structure incorrect for flashcards:", responseData);
                const errorMessage = responseData.message || 'API returned malformed data structure for flashcards';
                throw new Error(errorMessage);
            }

            const data: Flashcard[] = responseData.data.flashcards;
            console.log(`Parsed ${data.length} flashcards for file ID: ${fileId} (total count: ${responseData.count})`);
            
            if (data.length === 0) {
                console.warn(`No flashcards found for file ID: ${fileId}`);
            }
            
            setFlashcards(data);
        } catch (err) {
            const errorMsg = err instanceof Error ? err.message : 'An unknown error occurred';
            console.error(`Failed to fetch flashcards for file ID: ${fileId}:`, errorMsg);
            setFlashcardError(errorMsg);
        } finally {
            setIsLoadingFlashcards(false);
        }
    }, [user, fileId]); 

    const fetchQuizzes = useCallback(async () => {
        if (!user || !fileId) return;
        setIsLoadingQuizzes(true);
        setQuizError(null);
        
        try {
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');

            console.log(`Making API request to /api/v1/files/${fileId}/quizzes`);
            const response = await fetch(`/api/v1/files/${fileId}/quizzes`, {
                method: 'GET',
                headers: { Authorization: `Bearer ${idToken}` },
                mode: 'cors',
            });

            console.log(`Quizzes API response status: ${response.status}`);
            
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.detail || `Failed to fetch quizzes: ${response.statusText}`);
            }

            const responseData = await response.json();
            console.log(`Quizzes API response data:`, responseData);
            
            // Handle the standardized response format
            if (!responseData || responseData.status !== 'success' || !responseData.data || !Array.isArray(responseData.data.quizzes)) {
                console.error("API response structure incorrect for quizzes:", responseData);
                const errorMessage = responseData.message || 'API returned malformed data structure for quizzes';
                throw new Error(errorMessage);
            }

            const data: QuizQuestion[] = responseData.data.quizzes;
            console.log(`Parsed ${data.length} quizzes for file ID: ${fileId} (total count: ${responseData.count})`);
            
            if (data.length === 0) {
                console.warn(`No quizzes found for file ID: ${fileId}`);
            }
            
            setQuizzes(data);
        } catch (err) {
            const errorMsg = err instanceof Error ? err.message : 'An unknown error occurred';
            console.error(`Failed to fetch quizzes for file ID: ${fileId}:`, errorMsg);
            setQuizError(errorMsg);
        } finally {
            setIsLoadingQuizzes(false);
        }
    }, [user, fileId]); 

    useEffect(() => {
        // Set file type immediately to avoid null values in logs and UI
        const urlToTest = fileUrl || file.name; 
        const type = getFileType(urlToTest) || 'unknown';
        
        console.log(`FileViewerComponent: Input to getFileType: '${urlToTest}'. Detected type: '${type}'. File status: ${file.processing_status}`);
        
        // Set file type right away
        setFileType(type);

        if (type === 'unknown') {
            onError(`Unsupported file type or could not determine type for: ${file.name}`);
        }
        
        // Only fetch data if user is logged in, file ID exists, and file is not yet processed
        if (user && fileId) {
            // If the file is processed, we only need to fetch data once
            // If not processed, we continue polling for data
            if (file.processing_status !== 'processed') {
                console.log(`File ${fileId} is not yet processed (status: ${file.processing_status}). Fetching data...`);
                fetchKeyConcepts();
                fetchFlashcards();
                fetchQuizzes();
            } else {
                console.log(`File ${fileId} is already processed. Fetching data once...`);
                // Only fetch if we haven't loaded the data yet
                if (keyConcepts.length === 0) fetchKeyConcepts();
                if (flashcards.length === 0) fetchFlashcards();
                if (quizzes.length === 0) fetchQuizzes();
            }
        }
    }, [fileUrl, file.name, onError, file.processing_status, user, fileId]); 

    // We no longer need placeholder data since users can create their own content

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
            const newSet = new Set(prev);
            if (newSet.has(conceptId)) {
                newSet.delete(conceptId);
            } else {
                newSet.add(conceptId);
            }
            return newSet;
        });
    };
    
    const handleSortChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        setSortOrder(e.target.value as 'default' | 'alphabetical' | 'chronological');
    };

    const selectTab = (tabName: 'Key Concepts' | 'Flashcards' | 'Quiz') => {
        setTab(tabName);
    }

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
            addToast(`Navigated to page ${concept.source_page_number}`, 'info');
        } else if (concept.source_video_timestamp_start_seconds !== null && concept.source_video_timestamp_start_seconds !== undefined) {
            const startTimestamp = concept.source_video_timestamp_start_seconds;
            const endTimestamp = concept.source_video_timestamp_end_seconds;
            const hasDuration = endTimestamp !== null && endTimestamp !== undefined && endTimestamp > startTimestamp;
            
            // For notifications and highlighting
            const durationText = hasDuration
                ? `${formatVideoTimestamp(startTimestamp)} - ${formatVideoTimestamp(endTimestamp!)}`
                : `${formatVideoTimestamp(startTimestamp)}`;
            
            if (videoPlayerRef.current) {
                // For regular videos
                videoPlayerRef.current.currentTime = startTimestamp;
                videoPlayerRef.current.play();
                
                // If there's an end timestamp, setup a highlight timer for the duration
                if (hasDuration) {
                    const durationSeconds = endTimestamp! - startTimestamp;
                    addToast(`Playing segment (${durationText}, ${Math.round(durationSeconds)}s)`, 'info');
                    
                    // Keep the concept highlighted for the duration
                    if (concept.id) {
                        // Reset our timeout - keep highlighted for the full duration
                        clearTimeout(highlightTimeoutRef.current);
                        highlightTimeoutRef.current = setTimeout(() => setHighlightedConceptId(null), durationSeconds * 1000);
                    }
                } else {
                    addToast(`Jumped to ${durationText}`, 'info');
                }
            } else if (youtubePlayerRef.current && fileType === 'youtube') {
                // For YouTube videos
                youtubePlayerRef.current.seekTo(startTimestamp);
                youtubePlayerRef.current.playVideo();
                
                if (hasDuration) {
                    const durationSeconds = endTimestamp! - startTimestamp;
                    addToast(`Playing segment (${durationText}, ${Math.round(durationSeconds)}s)`, 'info');
                    
                    // Reset our timeout - keep highlighted for the full duration
                    clearTimeout(highlightTimeoutRef.current);
                    highlightTimeoutRef.current = setTimeout(() => setHighlightedConceptId(null), durationSeconds * 1000);
                } else {
                    addToast(`Jumped to ${durationText}`, 'info');
                }
            }
        }
        
        // Only clear highlight after animation if we're not playing a segment
        // The timeout will be managed separately for segments
        if (!concept.source_video_timestamp_end_seconds) {
            clearTimeout(highlightTimeoutRef.current);
            highlightTimeoutRef.current = setTimeout(() => setHighlightedConceptId(null), 2000);
        }
    };

    const renderFileContent = () => {
        // Handle loading and unprocessed states
        if (!fileType) {
            return <div className="loading-indicator">Loading file...</div>;
        }
        
        // Show loading indicator for unprocessed files
        if (file.processing_status !== 'processed') {
            const statusMessage = {
                'uploaded': 'File has been uploaded and is queued for processing.',
                'processing': 'File is being processed. Key concepts will appear when ready.',
                'extracted': 'File content has been extracted and is being analyzed.',
                'failed': 'File processing failed. Please try again later.'
            }[file.processing_status] || 'File is being processed...';
            
            return <div className="processing-indicator">{statusMessage}</div>;
        }

        // Handle different file types with a single return statement using conditional rendering
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
        } else if (fileType === 'pdf') {
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
        } else if (fileType === 'video') {
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
        } else if (fileType === 'image') {
            return (
                <div className="image-container file-content-area">
                    <img src={fileUrl} alt={file.name} className="image-viewer" />
                </div>
            );
        } else {
            return <div className="error-message">Unsupported file type: {fileType}</div>;
        }
    };

    const renderTabs = () => (
        <div className="tabs">
            {(['Key Concepts', 'Flashcards', 'Quiz'] as const).map((t) => (
                <button
                    key={t}
                    onClick={() => selectTab(t)}
                    className={`tab-button ${tab === t ? 'active' : ''}`}
                >
                    {t}
                    {t === 'Flashcards' && flashcards.length > 0 && ` (${flashcards.length})`}
                    {t === 'Quiz' && quizzes.length > 0 && ` (${quizzes.length})`}
                </button>
            ))}
        </div>
    );
    
    const renderKeyConcepts = () => {
        if (isLoadingKeyConcepts) {
            return <div className="loading-indicator">Loading key concepts...</div>;
        }

        if (error) {
            return <div className="error-message">{error}</div>;
        }

        if (file.processing_status !== 'processed') {
            const statusMessage = {
                'uploaded': 'File has been uploaded and is queued for processing.',
                'processing': 'File is being processed. Key concepts will appear when ready.',
                'extracted': 'File content has been extracted and is being analyzed.',
                'failed': 'File processing failed. Please try again later.'
            }[file.processing_status] || 'File is being processed...';
            
            return (
                <div className="key-concepts-container">
                    <p>{statusMessage}</p>
                </div>
            );
        }
        
        // Combine API-fetched key concepts with custom user-created ones
        const allKeyConcepts = [...keyConcepts, ...customKeyConcepts];
        
        if (showKeyConceptForm) {
            return renderKeyConceptForm();
        }
        
        if (!allKeyConcepts || allKeyConcepts.length === 0) {
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
            return (
                <div className="no-key-concepts">
                    <div className="action-buttons">
                        <button 
                            onClick={() => setShowKeyConceptForm(true)} 
                            className="create-button"
                        >
                            Create New Key Concept
                        </button>
                    </div>
                    <p>No key concepts available for this file. Create your own to get started!</p>
                </div>
            );
        }

        // Apply sorting based on user selection
        const sortedConcepts = sortKeyConcepts(allKeyConcepts);

        return (
            <div className="key-concepts-container">
                <div className="key-concepts-header">
                    <h3>Key Concepts</h3>
                    <div className="key-concepts-controls">
                        <button 
                            onClick={() => setShowKeyConceptForm(true)} 
                            className="create-button small"
                        >
                            Add Concept
                        </button>
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
                            className={`key-concept-card ${highlightedConceptId === concept.id ? 'concept-highlight' : ''} ${concept.is_custom ? 'custom-concept' : ''}`}
                        >
                            <div 
                                className="concept-header"
                                onClick={() => expandConcept(concept.id)}
                            >
                                <h4>
                                    {concept.concept_title || 'Untitled Concept'}
                                    {concept.is_custom && <span className="custom-badge">Custom</span>}
                                </h4>
                                <span className="expand-icon">
                                    {expandedConcepts.has(concept.id) ? "−" : "+"}
                                </span>
                            </div>
                            
                            {expandedConcepts.has(concept.id) && (
                                <div className="concept-content">
                                    <p>{concept.concept_explanation}</p>
                                    <SourceLink concept={concept} onNavigate={handleKeyConceptSourceClick} />
                                    <small>
                                        {concept.created_at ? (
                                            <em>Added: {new Date(concept.created_at).toLocaleString()}</em>
                                        ) : concept.is_custom ? (
                                            <em>Custom concept</em>
                                        ) : null}
                                    </small>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        );
    };

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
    
    // This SourceLink function was duplicated, removing the second instance
    
    // Add a new custom key concept with backend persistence
    const addKeyConcept = async () => {
        if (!user || newConceptTitle.trim() === '' || newConceptExplanation.trim() === '') return;
        
        // Create source information based on file type
        let sourcePage: number | null = null;
        let sourceVideoStart: number | null = null;
        let sourceVideoEnd: number | null = null;
        
        if (fileType === 'pdf' && newConceptSourcePage !== null) {
            sourcePage = newConceptSourcePage;
        } else if ((fileType === 'video' || fileType === 'youtube') && newConceptVideoStart !== null) {
            sourceVideoStart = newConceptVideoStart;
            sourceVideoEnd = newConceptVideoEnd; // May be null
        }
        
        // Create the concept object to send to the API
        const conceptData = {
            file_id: file.id,
            concept_title: newConceptTitle,
            concept_explanation: newConceptExplanation,
            source_page_number: sourcePage,
            source_video_timestamp_start_seconds: sourceVideoStart,
            source_video_timestamp_end_seconds: sourceVideoEnd,
            is_custom: true
        };
        
        try {
            // Show loading state
            setIsLoadingKeyConcepts(true);
            
            // Get authentication token
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');
            
            // Call the API to save the custom key concept
            const response = await fetch(`/api/v1/files/${file.id}/key_concepts`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`
                },
                mode: 'cors',
                body: JSON.stringify(conceptData)
            });
            
            if (!response.ok) {
                throw new Error(`Failed to save key concept: ${response.statusText}`);
            }
            
            // Get the saved concept with proper ID from the server
            const savedConcept: KeyConcept = await response.json();
            
            // Add the saved concept to our local state
            setCustomKeyConcepts([...customKeyConcepts, savedConcept]);
            
            // Reset form
            setNewConceptTitle('');
            setNewConceptExplanation('');
            setNewConceptSourcePage(null);
            setNewConceptVideoStart(null);
            setNewConceptVideoEnd(null);
            setShowKeyConceptForm(false);
        } catch (err) {
            // Handle errors
            console.error('Error saving custom key concept:', err);
            setError(err instanceof Error ? err.message : 'Failed to save key concept');
        } finally {
            // Hide loading state
            setIsLoadingKeyConcepts(false);
        }
    };
    
    // Render key concept creation form
    const renderKeyConceptForm = () => (
        <div className="creation-form">
            <h3>Create New Key Concept</h3>
            <div className="form-group">
                <label>Concept Title:</label>
                <input 
                    type="text"
                    value={newConceptTitle}
                    onChange={(e) => setNewConceptTitle(e.target.value)}
                    placeholder="Enter concept title..."
                />
            </div>
            <div className="form-group">
                <label>Explanation:</label>
                <textarea 
                    value={newConceptExplanation}
                    onChange={(e) => setNewConceptExplanation(e.target.value)}
                    placeholder="Enter explanation..."
                    rows={4}
                />
            </div>
            
            {/* Source location fields based on file type */}
            {fileType === 'pdf' && (
                <div className="form-group">
                    <label>Source Page Number:</label>
                    <input 
                        type="number"
                        min="1"
                        value={newConceptSourcePage || ''}
                        onChange={(e) => setNewConceptSourcePage(e.target.value ? parseInt(e.target.value) : null)}
                        placeholder="Page number"
                    />
                </div>
            )}
            
            {(fileType === 'video' || fileType === 'youtube') && (
                <>
                    <div className="form-group">
                        <label>Video Start Time (seconds):</label>
                        <input 
                            type="number"
                            min="0"
                            step="0.1"
                            value={newConceptVideoStart || ''}
                            onChange={(e) => setNewConceptVideoStart(e.target.value ? parseFloat(e.target.value) : null)}
                            placeholder="Start time in seconds"
                        />
                    </div>
                    <div className="form-group">
                        <label>Video End Time (seconds, optional):</label>
                        <input 
                            type="number"
                            min="0"
                            step="0.1"
                            value={newConceptVideoEnd || ''}
                            onChange={(e) => setNewConceptVideoEnd(e.target.value ? parseFloat(e.target.value) : null)}
                            placeholder="End time in seconds"
                        />
                    </div>
                </>
            )}
            
            <div className="form-buttons">
                <button onClick={() => setShowKeyConceptForm(false)}>Cancel</button>
                <button 
                    onClick={addKeyConcept} 
                    disabled={!newConceptTitle.trim() || !newConceptExplanation.trim()}
                >
                    Add Key Concept
                </button>
            </div>
        </div>
    );
    
    // Add a new custom flashcard to the collection with backend persistence
    const addFlashcard = async () => {
        if (!user || newFlashcardQuestion.trim() === '' || newFlashcardAnswer.trim() === '') return;
        
        const flashcardData = {
            file_id: file.id,
            question: newFlashcardQuestion,
            answer: newFlashcardAnswer,
            is_custom: true
        };
        
        try {
            // Show loading state
            setIsLoadingFlashcards(true);
            
            // Get authentication token
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');
            
            // Call the API to save the custom flashcard
            const response = await fetch(`/api/v1/files/${file.id}/flashcards`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`
                },
                mode: 'cors',
                body: JSON.stringify(flashcardData)
            });
            
            if (!response.ok) {
                throw new Error(`Failed to save flashcard: ${response.statusText}`);
            }
            
            // Get the saved flashcard with proper ID from the server
            const savedFlashcard: Flashcard = await response.json();
            
            // Add the saved flashcard to our local state
            setCustomFlashcards([...customFlashcards, savedFlashcard]);
            
            // Reset form
            setNewFlashcardQuestion('');
            setNewFlashcardAnswer('');
            setShowFlashcardForm(false);
        } catch (err) {
            // Handle errors
            console.error('Error saving custom flashcard:', err);
            setFlashcardError(err instanceof Error ? err.message : 'Failed to save flashcard');
        } finally {
            // Hide loading state
            setIsLoadingFlashcards(false);
        }
    };
    
    // Add a new custom quiz question to the collection with backend persistence
    const addQuiz = async () => {
        if (!user || newQuizQuestion.trim() === '' || newQuizAnswer.trim() === '') return;
        
        // For MCQ, we need distractors
        let distractors: string[] = [];
        if (newQuizType === 'MCQ') {
            distractors = newQuizDistractors.split(',').map(d => d.trim()).filter(d => d !== '');
            if (distractors.length === 0) return; // Need at least one distractor for MCQ
        }
        
        const quizData = {
            file_id: file.id,
            question: newQuizQuestion,
            question_type: newQuizType,
            correct_answer: newQuizAnswer,
            distractors: distractors,
            key_concept_id: 0, // Custom questions don't relate to specific key concepts
            is_custom: true
        };
        
        try {
            // Show loading state
            setIsLoadingQuizzes(true);
            
            // Get authentication token
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');
            
            // Call the API to save the custom quiz
            const response = await fetch(`/api/v1/files/${file.id}/quizzes`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`
                },
                mode: 'cors',
                body: JSON.stringify(quizData)
            });
            
            if (!response.ok) {
                throw new Error(`Failed to save quiz: ${response.statusText}`);
            }
            
            // Get the saved quiz with proper ID from the server
            const savedQuiz: QuizQuestion = await response.json();
            
            // Add the saved quiz to our local state
            setCustomQuizzes([...customQuizzes, savedQuiz]);
            
            // Reset form
            setNewQuizQuestion('');
            setNewQuizAnswer('');
            setNewQuizDistractors('');
            setShowQuizForm(false);
        } catch (err) {
            // Handle errors
            console.error('Error saving custom quiz:', err);
            setQuizError(err instanceof Error ? err.message : 'Failed to save quiz');
        } finally {
            // Hide loading state
            setIsLoadingQuizzes(false);
        }
    };
    

    
    // Render flashcard creation form
    const renderFlashcardForm = () => (
        <div className="creation-form">
            <h3>Create New Flashcard</h3>
            <div className="form-group">
                <label>Question:</label>
                <textarea 
                    value={newFlashcardQuestion}
                    onChange={(e) => setNewFlashcardQuestion(e.target.value)}
                    placeholder="Enter question..."
                />
            </div>
            <div className="form-group">
                <label>Answer:</label>
                <textarea 
                    value={newFlashcardAnswer}
                    onChange={(e) => setNewFlashcardAnswer(e.target.value)}
                    placeholder="Enter answer..."
                />
            </div>
            <div className="form-buttons">
                <button onClick={() => setShowFlashcardForm(false)}>Cancel</button>
                <button 
                    onClick={addFlashcard} 
                    disabled={!newFlashcardQuestion.trim() || !newFlashcardAnswer.trim()}
                >
                    Add Flashcard
                </button>
            </div>
        </div>
    );
    
    // Render quiz creation form
    const renderQuizForm = () => (
        <div className="creation-form">
            <h3>Create New Quiz Question</h3>
            <div className="form-group">
                <label>Question Type:</label>
                <select value={newQuizType} onChange={(e) => setNewQuizType(e.target.value as 'MCQ' | 'TF')}>
                    <option value="MCQ">Multiple Choice</option>
                    <option value="TF">True/False</option>
                </select>
            </div>
            <div className="form-group">
                <label>Question:</label>
                <textarea 
                    value={newQuizQuestion}
                    onChange={(e) => setNewQuizQuestion(e.target.value)}
                    placeholder="Enter question..."
                />
            </div>
            <div className="form-group">
                <label>Correct Answer:</label>
                {newQuizType === 'TF' ? (
                    <select value={newQuizAnswer} onChange={(e) => setNewQuizAnswer(e.target.value)}>
                        <option value="True">True</option>
                        <option value="False">False</option>
                    </select>
                ) : (
                    <input 
                        type="text" 
                        value={newQuizAnswer}
                        onChange={(e) => setNewQuizAnswer(e.target.value)}
                        placeholder="Enter correct answer..."
                    />
                )}
            </div>
            {newQuizType === 'MCQ' && (
                <div className="form-group">
                    <label>Distractors (comma-separated):</label>
                    <textarea 
                        value={newQuizDistractors}
                        onChange={(e) => setNewQuizDistractors(e.target.value)}
                        placeholder="Option 1, Option 2, Option 3..."
                    />
                </div>
            )}
            <div className="form-buttons">
                <button onClick={() => setShowQuizForm(false)}>Cancel</button>
                <button 
                    onClick={addQuiz} 
                    disabled={!newQuizQuestion.trim() || !newQuizAnswer.trim() || (newQuizType === 'MCQ' && !newQuizDistractors.trim())}
                >
                    Add Quiz Question
                </button>
            </div>
        </div>
    );

    const renderContent = () => {
        switch (tab) {
            case 'Key Concepts':
                return renderKeyConcepts();
                
            case 'Flashcards':
                // Combine API-fetched flashcards with custom user-created ones
                const allFlashcards = [...flashcards, ...customFlashcards];
                
                return isLoadingFlashcards ? 
                    <div className="loading">Loading flashcards...</div> :
                    <div className="content-container">
                        {showFlashcardForm ? (
                            renderFlashcardForm()
                        ) : (
                            <>
                                <div className="action-buttons">
                                    <button 
                                        onClick={() => setShowFlashcardForm(true)} 
                                        className="create-button"
                                    >
                                        Create New Flashcard
                                    </button>
                                </div>
                                {flashcardError ? (
                                    <div className="error-message">{flashcardError}</div>
                                ) : allFlashcards.length === 0 ? (
                                    <div className="no-content-message">
                                        No flashcards available. Create your own to get started!
                                    </div>
                                ) : (
                                    <FlashcardViewer flashcards={allFlashcards} />
                                )}
                            </>
                        )}
                    </div>;
                        
            case 'Quiz':
                // Combine API-fetched quizzes with custom user-created ones
                const allQuizzes = [...quizzes, ...customQuizzes];
                
                return isLoadingQuizzes ? 
                    <div className="loading">Loading quiz...</div> :
                    <div className="content-container">
                        {showQuizForm ? (
                            renderQuizForm()
                        ) : (
                            <>
                                <div className="action-buttons">
                                    <button 
                                        onClick={() => setShowQuizForm(true)} 
                                        className="create-button"
                                    >
                                        Create New Quiz Question
                                    </button>
                                </div>
                                {quizError ? (
                                    <div className="error-message">{quizError}</div>
                                ) : allQuizzes.length === 0 ? (
                                    <div className="no-content-message">
                                        No quiz questions available. Create your own to get started!
                                    </div>
                                ) : (
                                    <QuizInterface questions={allQuizzes} />
                                )}
                            </>
                        )}
                    </div>;
                        
            default:
                return <div>No content available for this tab</div>;
        }
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                {/* X close button in top right */}
                <button onClick={onClose} className="close-button">✕</button>
                
                <div className="file-viewer-header">
                    <h2>{file.name}</h2>
                </div>
                
                <div className="file-viewer-main-layout">
                    <div className="document-view-container">
                        {renderFileContent()}
                    </div>
                    <div className="side-panel">
                        <div className="tab-navigation-container">
                            <div className="tab-buttons">
                                <button 
                                    onClick={() => selectTab('Key Concepts')} 
                                    className={`tab-button ${tab === 'Key Concepts' ? 'active' : ''}`}
                                >
                                    Key Concepts
                                </button>
                                <button 
                                    onClick={() => selectTab('Flashcards')} 
                                    className={`tab-button ${tab === 'Flashcards' ? 'active' : ''}`}
                                >
                                    Flashcards
                                </button>
                                <button 
                                    onClick={() => selectTab('Quiz')} 
                                    className={`tab-button ${tab === 'Quiz' ? 'active' : ''}`}
                                >
                                    Quiz
                                </button>
                            </div>
                        </div>
                        <div className="tab-content">
                            <div className="current-tab-info">Current tab: {tab}</div>
                            {renderContent()}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default FileViewerComponent;