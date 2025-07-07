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
    const [editingConcept, setEditingConcept] = useState<KeyConcept | null>(null);
    
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
    const [customFlashcards, setCustomFlashcards] = useState<Flashcard[]>([]);
    const [quizzes, setQuizzes] = useState<QuizQuestion[]>([]);
    const [forceUpdate, setForceUpdate] = useState<boolean>(false);
    const [isLoadingFlashcards, setIsLoadingFlashcards] = useState(false);
    const [isLoadingQuizzes, setIsLoadingQuizzes] = useState(false);
    const [flashcardError, setFlashcardError] = useState<string | null>(null);
    const [quizError, setQuizError] = useState<string | null>(null);
    
    // State for custom flashcard and quiz creation
    // State for custom flashcard creation
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
    const fileUrl = file.file_url;

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

            console.log(`Making API request to /api/v1/files/${fileId}/key-concepts`);
            const response = await fetch(`/api/v1/files/${fileId}/key-concepts`, {
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
            if (!responseData || responseData.status !== 'success' || !responseData.data) {
                console.error("API response structure incorrect for key concepts:", responseData);
                const errorMessage = responseData?.message || 'API returned malformed data structure for key concepts';
                throw new Error(errorMessage);
            }

            // The data is wrapped in a KeyConceptsListResponse with a key_concepts array
            const keyConceptsData = responseData.data as { key_concepts: KeyConcept[] };
            const data = keyConceptsData.key_concepts || [];
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
            
            // A successful response can have an empty array of quizzes.
            if (!responseData || responseData.status !== 'success' || !responseData.data) {
                console.error("API response structure incorrect for quizzes:", responseData);
                const errorMessage = responseData?.message || 'API returned malformed data structure for quizzes';
                throw new Error(errorMessage);
            }

            // The data is wrapped in a QuizQuestionsListResponse with a quizzes array
            const quizzesData = responseData.data as { quizzes: QuizQuestion[] };
            const data = quizzesData.quizzes || [];
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
        // This effect determines and sets the file type whenever the file changes.
        const urlToTest = fileUrl || file.file_name;
        const type = getFileType(urlToTest) || 'unknown';
        
        console.log(`FileViewerComponent: Input to getFileType: '${urlToTest}'. Detected type: '${type}'.`);
        
        setFileType(type);
    }, [fileUrl, file.file_name]);

    useEffect(() => {
        // This effect handles data fetching and error reporting based on fileType and status.
        if (fileType === 'unknown' && (fileUrl || file.file_name)) {
            onError(`Unsupported file type or could not determine type for: ${file.file_name}`);
            return;
        }

        if (user && fileId && fileType !== 'unknown') {
            if (file.status !== 'processed') {
                console.log(`File ${fileId} is not yet processed (status: ${file.status}). Fetching data...`);
                fetchKeyConcepts();
                fetchFlashcards();
                fetchQuizzes();
            } else {
                console.log(`File ${fileId} is already processed. Fetching data once...`);
                if (keyConcepts.length === 0) fetchKeyConcepts();
                if (flashcards.length === 0) fetchFlashcards();
                if (quizzes.length === 0) fetchQuizzes();
            }
        }
    }, [fileType, file.status, user, fileId, keyConcepts.length, flashcards.length, quizzes.length, onError, fetchKeyConcepts, fetchFlashcards, fetchQuizzes, file.file_name, fileUrl]); 

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
        if (file.status !== 'processed') {
            const statusMessage = {
                'uploaded': 'File has been uploaded and is queued for processing.',
                'processing': 'File is being processed. Key concepts will appear when ready.',
                'extracted': 'File content has been extracted and is being analyzed.',
                'failed': 'File processing failed. Please try again later.'
            }[file.status] || 'File is being processed...';
            
            return <div className="processing-indicator">{statusMessage}</div>;
        }

        // Handle different file types with a single return statement using conditional rendering
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
                        onTimeUpdate={() => { /* Can potentially update state here if needed */ }}
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
    
    const handleEditConcept = (concept: KeyConcept) => {
        setEditingConcept(JSON.parse(JSON.stringify(concept)));
    };

    const handleCancelEdit = () => {
        setEditingConcept(null);
    };

    const handleSaveConcept = async (conceptId: number) => {
        if (!editingConcept) return;

        try {
            const token = await user?.getIdToken();
            const updateData: any = {
                concept_title: editingConcept.concept_title,
                concept_explanation: editingConcept.concept_explanation,
            };

            // Handle source fields based on file type
            if (fileType === 'pdf' && editingConcept.source_page_number !== null) {
                updateData.source_page_number = editingConcept.source_page_number;
            } else if ((fileType === 'video' || fileType === 'youtube') && editingConcept.source_video_timestamp_start_seconds !== null) {
                updateData.source_video_timestamp_start_seconds = editingConcept.source_video_timestamp_start_seconds;
                if (editingConcept.source_video_timestamp_end_seconds !== null) {
                    updateData.source_video_timestamp_end_seconds = editingConcept.source_video_timestamp_end_seconds;
                }
            }

            const response = await fetch(`/api/v1/files/key-concepts/${conceptId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify(updateData),
            });

            if (!response.ok) {
                throw new Error('Failed to save concept');
            }

            const updatedConcept = (await response.json()).data;
            setKeyConcepts(keyConcepts.map(c => c.id === updatedConcept.id ? updatedConcept : c));
            setEditingConcept(null);
            addToast('Key concept saved!', 'success');
        } catch (error) {
            console.error('Save error:', error);
            addToast('Error saving concept', 'error');
        }
    };

    const handleDeleteConcept = async (conceptId: number) => {
        if (window.confirm('Are you sure you want to delete this key concept?')) {
            try {
                const token = await user?.getIdToken();
                const response = await fetch(`/api/v1/files/key-concepts/${conceptId}`, {
                    method: 'DELETE',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                    },
                });

                if (!response.ok) {
                    throw new Error('Failed to delete concept');
                }

                setKeyConcepts(keyConcepts.filter(c => c.id !== conceptId));
                addToast('Key concept deleted!', 'success');
            } catch (error) {
                console.error('Delete error:', error);
                addToast('Error deleting concept', 'error');
            }
        }
    };

    const renderKeyConcepts = () => {
        if (isLoadingKeyConcepts) {
            return <div className="loading-indicator">Loading key concepts...</div>;
        }

        if (error) {
            return <div className="error-message">{error}</div>;
        }

        if (file.status !== 'processed') {
            const statusMessage = {
                'uploaded': 'File has been uploaded and is queued for processing.',
                'processing': 'File is being processed. Key concepts will appear when ready.',
                'extracted': 'File content has been extracted and is being analyzed.',
                'failed': 'File processing failed. Please try again later.'
            }[file.status] || 'File is being processed...';
            
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
                            className={`key-concept-card ${highlightedConceptId === concept.id ? 'concept-highlight' : ''} ${concept.is_custom ? 'custom-concept' : ''}`}>
                            {editingConcept && editingConcept.id === concept.id ? (
                                <div className="key-concept-edit-form">
                                    <input 
                                        type="text" 
                                        value={editingConcept.concept_title || ''}
                                        onChange={(e) => setEditingConcept({...editingConcept, concept_title: e.target.value})}
                                        className="edit-concept-title-input"
                                    />
                                    <textarea 
                                        value={editingConcept.concept_explanation || ''}
                                        onChange={(e) => setEditingConcept({...editingConcept, concept_explanation: e.target.value})}
                                        className="edit-concept-explanation-textarea"
                                    />
                                    <div className="edit-actions">
                                        <button onClick={() => handleSaveConcept(concept.id)} className="save-btn">Save</button>
                                        <button onClick={handleCancelEdit} className="cancel-btn">Cancel</button>
                                    </div>
                                </div>
                            ) : (
                                <>
                                    <div className="concept-header" onClick={() => expandConcept(concept.id)}>
                                        <h4>
                                            {concept.concept_title || 'Untitled Concept'}
                                            {concept.is_custom && <span className="custom-badge">Custom</span>}
                                        </h4>
                                        <div className="key-concept-actions">
                                            <button onClick={(e) => {e.stopPropagation(); handleEditConcept(concept);}} className="action-btn edit-btn">
                                                <span className="icon">✏️</span>
                                                <span className="text">Edit</span>
                                            </button>
                                            <button onClick={(e) => {e.stopPropagation(); handleDeleteConcept(concept.id);}} className="action-btn delete-btn">
                                                <span className="icon">🗑️</span>
                                                <span className="text">Delete</span>
                                            </button>
                                            <span className="expand-icon">
                                                {expandedConcepts.has(concept.id) ? "−" : "+"}
                                            </span>
                                        </div>
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
                                </>
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
        
        // Create source data based on file type
        const conceptData: any = {
            concept_title: newConceptTitle,
            concept_explanation: newConceptExplanation,
            is_custom: true
        };

        // Add source location based on file type
        if (fileType === 'pdf' && newConceptSourcePage !== null) {
            conceptData.source_page_number = newConceptSourcePage;
        } else if ((fileType === 'video' || fileType === 'youtube') && newConceptVideoStart !== null) {
            conceptData.source_video_timestamp_start_seconds = newConceptVideoStart;
            if (newConceptVideoEnd !== null) {
                conceptData.source_video_timestamp_end_seconds = newConceptVideoEnd;
            }
        }
        
        try {
            setIsLoadingKeyConcepts(true);
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');
            
            const response = await fetch(`/api/v1/files/${file.id}/key-concepts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${idToken}` },
                mode: 'cors',
                body: JSON.stringify(conceptData)
            });
            
            let responseData;
            try {
                responseData = await response.json();
            } catch (e) {
                console.error('Failed to parse error response:', e);
                throw new Error(`Failed to save key concept: ${response.statusText} (${response.status})`);
            }
            
            if (!response.ok) {
                console.error('Error response from server:', responseData);
                let errorMessage = 'Failed to save key concept';
                
                // Handle different error response formats
                if (Array.isArray(responseData.detail)) {
                    // If detail is an array, join all error messages
                    errorMessage = responseData.detail.map((err: any) => 
                        typeof err === 'string' ? err : 
                        err.msg ? `${err.msg} (${err.loc ? err.loc.join('.') : 'field'})` : 
                        JSON.stringify(err)
                    ).join('; ');
                } else if (typeof responseData.detail === 'string') {
                    errorMessage = responseData.detail;
                } else if (responseData.message) {
                    errorMessage = responseData.message;
                } else if (responseData.error) {
                    errorMessage = responseData.error;
                } else {
                    errorMessage = `${response.statusText} (${response.status})`;
                }
                
                throw new Error(errorMessage);
            }
            
            if (responseData.status !== 'success' || !responseData.data) {
                console.error('Invalid response format:', responseData);
                throw new Error(responseData.message || 'Invalid response format from server');
            }
            
            const savedConcept: KeyConcept = responseData.data;
            
            setCustomKeyConcepts(prev => [...prev, savedConcept]);
            
            setNewConceptTitle('');
            setNewConceptExplanation('');
            setNewConceptSourcePage(null);
            setNewConceptVideoStart(null);
            setNewConceptVideoEnd(null);
            setShowKeyConceptForm(false);
        } catch (err) {
            console.error('Error saving custom key concept:', err);
            setError(err instanceof Error ? err.message : 'Failed to save key concept');
        } finally {
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
            setIsLoadingFlashcards(true);
            
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');
            
            const response = await fetch(`/api/v1/files/${file.id}/flashcards`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${idToken}` },
                mode: 'cors',
                body: JSON.stringify(flashcardData)
            });
            
            let responseData;
            try {
                responseData = await response.json();
            } catch (e) {
                console.error('Failed to parse response:', e);
                throw new Error(`Failed to save flashcard: ${response.statusText} (${response.status})`);
            }
            
            if (!response.ok) {
                console.error('Error response from server:', responseData);
                const errorMessage = responseData.detail || 
                                  responseData.message || 
                                  responseData.error || 
                                  `Failed to save flashcard: ${response.statusText} (${response.status})`;
                throw new Error(errorMessage);
            }
            
            if (responseData.status !== 'success' || !responseData.data) {
                console.error('Invalid response format:', responseData);
                throw new Error(responseData.message || 'Invalid response format from server');
            }
            
            const savedFlashcard: Flashcard = responseData.data;
            
            setFlashcards(prev => [...prev, savedFlashcard]);
            setCustomFlashcards(prev => [...prev, savedFlashcard]);
            
            setNewFlashcardQuestion('');
            setNewFlashcardAnswer('');
            setShowFlashcardForm(false);
            
            // Show success message
            addToast('Flashcard added successfully!', 'success');
        } catch (err) {
            const errorMessage = err instanceof Error ? err.message : 'Failed to save flashcard';
            console.error('Error saving custom flashcard:', errorMessage, err);
            setFlashcardError(errorMessage);
            addToast(errorMessage, 'error');
        } finally {
            setIsLoadingFlashcards(false);
        }
    };

    // Add a new custom quiz question to the collection with backend persistence
    const addQuiz = async () => {
        if (!user || newQuizQuestion.trim() === '' || newQuizAnswer.trim() === '') return;

        let distractors: string[] = [];
        if (newQuizType === 'MCQ') {
            distractors = newQuizDistractors.split(',').map(d => d.trim()).filter(d => d !== '');
            if (distractors.length === 0) {
                addToast('MCQ questions must have at least one distractor.', 'error');
                return; 
            }
        } else if (newQuizType === 'TF') {
            distractors = [newQuizAnswer === 'True' ? 'False' : 'True'];
        }
        
        const quizData = {
            file_id: file.id,
            question: newQuizQuestion,
            question_type: newQuizType,
            correct_answer: newQuizAnswer,
            distractors: distractors,
            key_concept_id: 0, // Custom questions don't relate to specific key concepts
            is_custom: true,
            difficulty: 'medium' // Add default difficulty
        };
        
        try {
            setIsLoadingQuizzes(true);
            
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');
            
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
                const errorData = await response.json().catch(() => ({ detail: `Failed to save quiz: ${response.statusText}` }));
                console.error("Error saving custom quiz:", errorData);
                throw new Error(errorData.detail || `Failed to save quiz: ${response.statusText}`);
            }
            
            const responseData = await response.json();
            if (responseData.status !== 'success' || !responseData.data) {
                throw new Error(responseData.message || 'Failed to parse successful response from server.');
            }
            const savedQuiz: QuizQuestion = responseData.data;
            
            addToast('Quiz question added successfully!', 'success');
            setQuizzes(prevQuizzes => [...prevQuizzes, savedQuiz]);
            
            setNewQuizQuestion('');
            setNewQuizAnswer('');
            setNewQuizDistractors('');
            setNewQuizType('MCQ');
            setShowQuizForm(false);
        } catch (err) {
            const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
            console.error('Error saving custom quiz:', err);
            addToast(`Error saving custom quiz: ${errorMessage}`, 'error');
            setQuizError(errorMessage);
        } finally {
            setIsLoadingQuizzes(false);
        }
    };

    // Render quiz creation form
    const renderQuizForm = () => (
        <div className="creation-form">
            <h3>Create New Quiz Question</h3>
            <div className="form-group">
                <label>Question Type:</label>
                <select value={newQuizType} onChange={(e) => {
                    const type = e.target.value as 'MCQ' | 'TF';
                    setNewQuizType(type);
                    if (type === 'TF') {
                        setNewQuizAnswer('True'); // Default answer to enable button
                    } else {
                        setNewQuizAnswer(''); // Clear for MCQ
                    }
                }}>
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

    const handleUpdateFlashcard = async (id: number, data: { question?: string; answer?: string }) => {
        if (!user) {
            addToast("You must be logged in to update a flashcard.", "error");
            return;
        }
        try {
            console.log('Updating flashcard with ID:', id, 'Data:', data);
            const token = await user.getIdToken();
            const response = await fetch(`/api/v1/files/flashcards/${id}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify(data),
            });

            let responseData;
            try {
                responseData = await response.json();
                console.log('Raw response data:', responseData);
            } catch (e) {
                console.error('Failed to parse response:', e);
                throw new Error(`Failed to update flashcard: ${response.statusText} (${response.status})`);
            }

            if (!response.ok) {
                console.error('Error response from server:', responseData);
                const errorMessage = responseData.detail || 
                                  responseData.message || 
                                  responseData.error || 
                                  `Failed to update flashcard: ${response.statusText} (${response.status})`;
                throw new Error(errorMessage);
            }

            if (responseData.status !== 'success' || !responseData.data) {
                console.error('Invalid response format:', responseData);
                throw new Error(responseData.message || 'Invalid response format from server');
            }

            const updatedFlashcard = responseData.data;
            console.log('Updated flashcard data from server:', updatedFlashcard);
            
            // Log current state before update
            console.log('Current flashcards state before update:', flashcards);
            console.log('Current customFlashcards state before update:', customFlashcards);
            
            // Update both flashcards and customFlashcards states
            setFlashcards(prev => {
                const updated = prev.map(fc => {
                    if (fc.id === id) {
                        console.log('Updating flashcard in flashcards state. ID:', id, 'Old:', fc, 'New:', { ...fc, ...updatedFlashcard });
                        return { ...fc, ...updatedFlashcard };
                    }
                    return fc;
                });
                console.log('Updated flashcards state:', updated);
                return updated;
            });
            
            setCustomFlashcards(prev => {
                const updated = prev.map(fc => {
                    if (fc.id === id) {
                        console.log('Updating flashcard in customFlashcards state. ID:', id, 'Old:', fc, 'New:', { ...fc, ...updatedFlashcard });
                        return { ...fc, ...updatedFlashcard };
                    }
                    return fc;
                });
                console.log('Updated customFlashcards state:', updated);
                return updated;
            });
            
            // Force a re-render by toggling a dummy state
            setForceUpdate(prev => !prev);
            
            addToast("Flashcard updated successfully!", "success");
        } catch (error) {
            console.error("Error updating flashcard:", error);
            const message = error instanceof Error ? error.message : "An error occurred while updating the flashcard.";
            addToast(message, "error");
        }
    };

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

    const handleDeleteFlashcard = async (id: number) => {
        if (!user) {
            addToast("You must be logged in to delete a flashcard.", "error");
            return;
        }
        try {
            const token = await user.getIdToken();
            const response = await fetch(`/api/v1/files/flashcards/${id}`,
                {
                    method: 'DELETE',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                    },
                }
            );

            if (!response.ok) {
                throw new Error(`Failed to delete flashcard. Server responded with ${response.status}`);
            }

            setFlashcards(prev => prev.filter(fc => fc.id !== id));
            setCustomFlashcards(prev => prev.filter(fc => fc.id !== id));
            addToast("Flashcard deleted successfully!", "success");
        } catch (error) {
            console.error("Error deleting flashcard:", error);
            let message = "An error occurred while deleting the flashcard.";
            if (error instanceof Error) {
                message = error.message;
            }
            addToast(message, "error");
        }
    };

    const handleUpdateQuiz = async (id: number, data: Partial<QuizQuestion>) => {
        if (!user) {
            addToast("You must be logged in to update a quiz question.", "error");
            return;
        }
        try {
            const token = await user.getIdToken();
            
            // Ensure we're using the correct field names for the backend
            const updateData: any = { ...data };
            
            // Handle case where frontend might be using 'answer' instead of 'correct_answer'
            if ('answer' in updateData && !('correct_answer' in updateData)) {
                updateData.correct_answer = updateData.answer;
                delete updateData.answer;
            }
            
            // Ensure we have a default difficulty if not provided
            if (!('difficulty' in updateData)) {
                updateData.difficulty = 'medium';
            }
            
            const response = await fetch(`/api/v1/files/quizzes/${id}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify(updateData),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to update quiz question');
            }

            const updatedQuizResponse = await response.json();
            const updatedQuizQuestion = updatedQuizResponse.data;

            setQuizzes((prev: QuizQuestion[]) => prev.map(q => (q.id === id ? { ...q, ...updatedQuizQuestion } : q)));
            setCustomQuizzes((prev: QuizQuestion[]) => prev.map(q => (q.id === id ? { ...q, ...updatedQuizQuestion } : q)));

            addToast("Quiz question updated successfully!", "success");
        } catch (error) {
            console.error("Error updating quiz question:", error);
            let message = "An error occurred while updating the quiz question.";
            if (error instanceof Error) {
                message = error.message;
            }
            addToast(message, "error");
        }
    };

    const handleDeleteQuiz = async (id: number) => {
        if (!user) {
            addToast("You must be logged in to delete a quiz question.", "error");
            return;
        }
        try {
            const token = await user.getIdToken();
            const response = await fetch(`/api/v1/files/quizzes/${id}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${token}`,
                },
            });

            if (!response.ok) {
                throw new Error(`Failed to delete quiz question. Server responded with ${response.status}`);
            }

            setQuizzes((prev: QuizQuestion[]) => prev.filter(q => q.id !== id));
            setCustomQuizzes((prev: QuizQuestion[]) => prev.filter(q => q.id !== id));
            addToast("Quiz question deleted successfully!", "success");
        } catch (error) {
            console.error("Error deleting quiz question:", error);
            let message = "An error occurred while deleting the quiz question.";
            if (error instanceof Error) {
                message = error.message;
            }
            addToast(message, "error");
        }
    };

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
                                    <FlashcardViewer 
                                        flashcards={allFlashcards}
                                        onUpdateFlashcard={handleUpdateFlashcard}
                                        onDeleteFlashcard={handleDeleteFlashcard}
                                    />
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
                                    <QuizInterface questions={allQuizzes} onUpdateQuiz={handleUpdateQuiz} onDeleteQuiz={handleDeleteQuiz} />
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
                    <h2>{file.file_name}</h2>
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