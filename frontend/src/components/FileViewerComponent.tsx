import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useUserContext } from '../UserContext';
import './FileViewerComponent.css';
import { UploadedFile, Explanation } from './types';
import SelectionToolbox from './SelectionToolbox'; // Import the new component

interface FileViewerComponentProps {
    file: UploadedFile;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}


const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ file, onClose, onError, darkMode }) => {
    const { user } = useUserContext();
    const [fileType, setFileType] = useState<string | null>(null);
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [showExplainPanel, setShowExplainPanel] = useState<boolean>(false);
    const [isExplaining, setIsExplaining] = useState<boolean>(false);
    const [explanationHistory, setExplanationHistory] = useState<Explanation[]>([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);
    const [selectionToolboxPosition, setSelectionToolboxPosition] = useState<{ top: number; left: number } | null>(null);
    const [selectionText, setSelectionText] = useState<string>('');
    const [isPremiumUser, setIsPremiumUser] = useState<boolean>(false);
    const [showTools, setShowTools] = useState(false);
    const [isExplanationLoading, setIsExplanationLoading] = useState<boolean>(false);
    const [explanation, setExplanation] = useState<string | null>(null);
    const [debugSelection, setDebugSelection] = useState<string>(""); // State to show debug info
    const [allExplanations, setAllExplanations] = useState<any[]>([]); // State to store all fetched explanations

    const pdfViewerRef = useRef<HTMLIFrameElement>(null);
    const videoPlayerRef = useRef<HTMLVideoElement>(null);

    const fileId = file.id;
    const fileUrl = file.publicUrl;

    const getFileType = (urlOrName: string): string | null => {
        const extension = urlOrName.split('.').pop()?.toLowerCase();
        if (!extension) return null;
        if (extension === 'pdf') return 'pdf';
        if (['mp4', 'webm', 'ogg', 'mov'].includes(extension)) return 'video';
        if (['jpg', 'jpeg', 'png', 'gif'].includes(extension)) return 'image';
        return null;
    };

    const fetchExplanationHistory = useCallback(async () => {
        if (!user || !fileId) return;
        setIsLoadingHistory(true);
        setError(null);
        try {
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');

            const response = await fetch(`/api/v1/files/${fileId}/explanations`, {
                method: 'GET',
                headers: { Authorization: `Bearer ${idToken}` },
                mode: 'cors',
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.detail || `Failed to fetch history: ${response.statusText}`);
            }

            const responseData = await response.json();
            if (!responseData || !Array.isArray(responseData.explanations)) {
                console.error("API response structure incorrect:", responseData); // Log the actual bad response
                throw new Error('API returned malformed data structure'); // More specific error
            }

            const data: Explanation[] = responseData.explanations; // Extract the array

            setExplanationHistory(data);
        } catch (err) {
            console.error('Error fetching explanation history:', err);
            const errorMsg = err instanceof Error ? err.message : 'Failed to load explanation history.';
            setError(errorMsg);
            onError(errorMsg); // Notify parent
            setExplanationHistory([]); // Clear history on error
        } finally {
            setIsLoadingHistory(false);
        }
    }, [user, fileId, onError]); // Dependencies for the fetch function


    useEffect(() => {
        const type = getFileType(fileUrl || file.name);
        console.log(`Detected file type: ${type}`); // Debugging statement
        setFileType(type);
        if (!type) {
            onError(`Unsupported file type or could not determine type for: ${file.name}`);
        }
    }, [fileUrl, file.name, onError]);
    
    useEffect(() => {
        if (showExplainPanel && user && fileId) {
            fetchExplanationHistory();
        }
        // Clear history if the panel is closed or file changes
        if (!showExplainPanel) {
            // Optionally clear history when panel closes, or keep it cached in state
            // setExplanationHistory([]);
        }
    }, [showExplainPanel, user, fileId, fetchExplanationHistory]);

    const createExplanation = async (variables: { startTime?: number; endTime?: number }) => {
        if (!user) {
            setError('User not available');
            onError('User not available');
            return;
        }
        if (!fileType || (fileType !== 'pdf' && fileType !== 'video')) {
            setError('Unsupported file type for explanation.');
            onError('Unsupported file type for explanation.');
            return;
        }

        setIsExplaining(true);
        setError(null);
        try {
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not found');

            // Build correct payload for backend
            const apiPayload = {
                selection_type: fileType === 'pdf' ? 'text' : 'video_range',
                page: fileType === 'pdf' ? currentPage : undefined,
                video_start: fileType === 'video' ? variables.startTime : undefined,
                video_end: fileType === 'video' ? variables.endTime : undefined,
            };

            const response = await fetch(`/api/v1/files/${fileId}/explain`, {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${idToken}`,
                    'Content-Type': 'application/json',
                },
                mode: 'cors',
                body: JSON.stringify(apiPayload),
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.detail || `Failed to explain: ${response.statusText}`);
            }

            const newExplanation: Explanation = await response.json();
            setExplanationHistory((prev) =>
                [newExplanation, ...prev].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
            );
            if (!showExplainPanel) setShowExplainPanel(true); // Ensure panel is visible
        } catch (error) {
            console.error('Explanation creation error:', error);
            const errorMsg = error instanceof Error ? error.message : 'An unknown error occurred during explanation.';
            setError(errorMsg);
            onError(errorMsg);
        } finally {
            setIsExplaining(false);
        }
    };

    const handleExplainClick = async () => {
        setShowTools(false);
        setError(null);
        setExplanation(null);
        setIsExplanationLoading(true);

        if (!isPremiumUser) {
            setError("Explain feature requires a premium subscription.");
            setIsExplanationLoading(false);
            return;
        }

        try {
            let foundExplanation = null;

            if (fileType === 'pdf') {
                console.log(`Finding PDF explanation for page: ${currentPage}`);
                // Find explanation matching the current page
                foundExplanation = allExplanations.find(
                    exp => exp.selection_type === 'text' && exp.page === currentPage
                );
                if (!foundExplanation) {
                    console.warn(`No pre-generated explanation found for page ${currentPage}.`);
                }

            } else if (fileType === 'video' && videoPlayerRef.current) {
                const currentTime = videoPlayerRef.current.currentTime;
                console.log(`Finding Video explanation for time: ${currentTime}`);
                // Find explanation where currentTime is within start/end or closest to start
                // This logic might need refinement based on how segments/explanations are stored
                foundExplanation = allExplanations
                    .filter(exp => exp.selection_type === 'video_range' && exp.video_start !== null)
                    .sort((a, b) => Math.abs(a.video_start - currentTime) - Math.abs(b.video_start - currentTime))
                    .find(exp => currentTime >= exp.video_start && (exp.video_end === null || currentTime <= exp.video_end));

                // Fallback to closest start time if no exact match
                if (!foundExplanation && allExplanations.length > 0) {
                    foundExplanation = allExplanations
                        .filter(exp => exp.selection_type === 'video_range' && exp.video_start !== null)
                        .sort((a, b) => Math.abs(a.video_start - currentTime) - Math.abs(b.video_start - currentTime))[0];
                    console.warn(`No exact video range match for ${currentTime}s. Using closest explanation starting at ${foundExplanation?.video_start}s.`);
                }

                if (!foundExplanation) {
                    console.warn(`No pre-generated explanation found near time ${currentTime}s.`);
                }
            }

            if (foundExplanation && foundExplanation.explanation_text) {
                console.log("Displaying pre-generated explanation:", foundExplanation);
                setExplanation(foundExplanation.explanation_text);
            } else {
                // No specific explanation found for this page/time
                if (allExplanations.length > 0 || !isExplanationLoading) {
                    // We have explanations loaded, just not for this specific selection
                    const identifierValue = fileType === 'pdf' ? currentPage : (videoPlayerRef.current?.currentTime ?? 0);
                    const selectionType = fileType === 'pdf' ? `page ${identifierValue}` : `time ${Number(identifierValue).toFixed(1)}s`;
                    setError(`No pre-generated explanation found for ${selectionType}.`);
                } else {
                    // This might mean explanations failed to load or haven't loaded yet
                    setError("Explanations are not available for this file or failed to load.");
                }
                setExplanation(null); // Clear any old explanation being displayed
            }

        } catch (error) {
            console.error('Error during explanation retrieval:', error);
            let errorMessage = 'An unexpected error occurred while finding the explanation.';
            if (error instanceof Error) {
                errorMessage = error.message;
            }
            setError(errorMessage);
        } finally {
            setIsExplanationLoading(false);
        }
    };

    const fetchExplanations = async () => {
        if (!fileId || !user) return;
        setIsExplanationLoading(true); // Use existing loading state or add a new one
        setAllExplanations([]); // Clear previous explanations
        try {
            const idToken = await user.getIdToken();
            if (!idToken) throw new Error('User token not available');

            const response = await fetch(`/api/v1/files/${fileId}/explanations`, {
                headers: {
                    'Authorization': `Bearer ${idToken}`
                }
            });
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
            }
            const data = await response.json();
            setAllExplanations(data.explanations || []); // Update state with fetched explanations
            console.log("Fetched explanations:", data.explanations);
        } catch (error) {
            console.error('Error fetching explanations:', error);
            setError('Failed to load explanations.');
            // Optionally set error state to show in UI
        } finally {
            setIsExplanationLoading(false);
        }
    };

    useEffect(() => {
        fetchExplanations();
    }, [fileId, user]);

    const handlePdfMessage = useCallback((event: MessageEvent) => {
        const expectedOrigin = 'expected_pdf_viewer_origin'; // Replace with dynamic origin if needed
        if (event.origin !== expectedOrigin) return;

        const data = event.data;
        if (data && typeof data === 'object' && data.type === 'pageChange') {
            if (typeof data.pageNumber === 'number') {
                setCurrentPage(data.pageNumber);
                console.log("PDF Page changed to:", data.pageNumber);
            }
        }
    }, []);

    useEffect(() => {
        window.addEventListener('message', handlePdfMessage);
        return () => {
            window.removeEventListener('message', handlePdfMessage);
        };
    }, [handlePdfMessage]);

    const renderFileContent = () => {
        if (!fileType) {
            return <div className="loading-indicator">Loading file...</div>;
        }

        if (/^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//.test(file.publicUrl || file.name)) {
            // Extract YouTube video ID from URL
            let videoId = '';
            const url = file.publicUrl || '';
            const match = url.match(/(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|embed)\/|.*[?&]v=)|youtu\.be\/)([\w-]{11})/);
            if (match && match[1]) {
                videoId = match[1];
            }
            return (
                <div className="youtube-container file-content-area">
                    <iframe
                        width="100%"
                        height="400"
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

    const handleExplainSelection = useCallback(async () => {
        // Ensure user and selectionText exist before trying to get token
        if (!selectionText || !file || !user) {
            console.error('User, file, or selection text missing.');
            return;
        }
        console.log('Explain clicked for text:', selectionText);
        try {
            // Get the ID token asynchronously
            const idToken = await user.getIdToken();
            if (!idToken) {
                throw new Error('Failed to get ID token.');
            }

            const response = await fetch(`/api/v1/files/${file.id}/explain`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
                body: JSON.stringify({
                    selection_type: 'text',
                    page: currentPage,
                    video_start: null,
                    video_end: null,
                }),
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            console.log('Explanation received:', data);
            alert(`Explanation: ${data.explanation_text || data.explanation}`); // Placeholder display
        } catch (error) {
            console.error('Error explaining selection:', error);
            alert('Failed to get explanation.');
        }
    }, [selectionText, file, user, currentPage]);

    const handleCloseToolbox = useCallback(() => {
        setSelectionToolboxPosition(null); // Hide toolbox
        setSelectionText(''); // Clear selected text state
    }, []);

    useEffect(() => {
        const handleMouseUp = (event: MouseEvent) => {
            const selection = window.getSelection();
            const selectedText = selection?.toString().trim();

            // Check if selection exists and has text before proceeding
            if (selectedText && selection && pdfViewerRef.current?.contains(event.target as Node)) { 
                const range = selection.getRangeAt(0); 
                const rect = range.getBoundingClientRect();
                const containerRect = pdfViewerRef.current.getBoundingClientRect();
                const top = rect.bottom - containerRect.top + window.scrollY + 5;
                const left = rect.left - containerRect.left + window.scrollX + rect.width / 2;

                setSelectionText(selectedText);
                setSelectionToolboxPosition({ top, left });
            } else {
                if (!(event.target as Element)?.closest('.selection-toolbox')) {
                    handleCloseToolbox();
                }
            }
        };

        const iframeElement = pdfViewerRef.current;
        if (fileType === 'pdf' && iframeElement) {
            const timerId = setTimeout(() => { document.addEventListener('mouseup', handleMouseUp); }, 100);
            return () => {
                clearTimeout(timerId);
                document.removeEventListener('mouseup', handleMouseUp);
            };
        }
        // Ensure listener is removed if conditions aren't met or component unmounts
        return () => { document.removeEventListener('mouseup', handleMouseUp); };
    }, [fileType, handleCloseToolbox]);

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                <div className="file-viewer-main-content">
                    <div className="viewer-controls">
                        <button
                            onClick={() => {
                                if (!showExplainPanel) {
                                    setShowExplainPanel(true);
                                    handleExplainClick();
                                } else {
                                    setShowExplainPanel(false);
                                }
                            }}
                            className={`toolbar-button explain-button ${showExplainPanel ? 'active' : ''}`}
                            title={showExplainPanel ? "Hide Explanations / Explain Again" : "Show Explanations / Explain Current View"}
                            disabled={isExplaining || isLoadingHistory}
                            aria-label={showExplainPanel ? "Hide Explanations / Explain Again" : "Show Explanations / Explain Current View"}
                        >
                            {isExplaining ? '⏳' : '✨'} {/* Use emojis: Sparkles for Explain, Hourglass for Loading */}
                        </button>
                        <button
                            onClick={onClose}
                            className="toolbar-button close-button"
                            title="Close Viewer"
                            aria-label="Close file viewer"
                        >
                            ❌ {/* Use emoji: Cross Mark for Close */}
                        </button>
                    </div>

                    <div className="document-view">
                        {renderFileContent()}
                    </div>

                    {showExplainPanel && (
                        <div className="explanation-panel">
                            <h3>Explanation History</h3>
                            {isLoadingHistory && <p>Loading history...</p>}
                            {error && <p className="error-message">{error}</p>}
                            {isExplaining && <p>Generating new explanation...</p>}
                            <div className="explanation-list">
                                {explanationHistory.length > 0 ? (
                                    explanationHistory.map((exp) => (
                                        <div key={exp.id} className="explanation-item">
                                            <p><strong>Context:</strong> {exp.context_info || (exp.page ? `Page ${exp.page}` : (exp.video_start !== null ? `Time ${exp.video_start?.toFixed(1)}s` : 'General'))}</p>
                                            <p>{exp.explanation_text}</p>
                                            <small>{new Date(exp.created_at).toLocaleString()}</small>
                                        </div>
                                    ))
                                ) : (
                                    !isLoadingHistory && !isExplaining && <p>No explanations yet for this file.</p>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
            <SelectionToolbox 
                position={selectionToolboxPosition} 
                onExplain={handleExplainSelection} 
                onClose={handleCloseToolbox} 
            />
        </div>
    );
}

export default FileViewerComponent;
