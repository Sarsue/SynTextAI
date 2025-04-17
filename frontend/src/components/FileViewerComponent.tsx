import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useUserContext } from '../UserContext';
import './FileViewerComponent.css';
import { UploadedFile } from './types';
import SelectionToolbox from './SelectionToolbox'; // Import the new component

interface FileViewerComponentProps {
    file: UploadedFile;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}

interface Explanation {
    id: number;
    context_info: string | null;
    explanation_text: string;
    created_at: string;
    page: number | null;
    video_start?: number | null;
    video_end?: number | null;
}

interface ExplainApiPayload {
    fileId: number;
    contentType: 'pdf' | 'video';
    pageNumber?: number;
    startTime?: number;
    endTime?: number;
}

interface ExplainMutationVariables {
    contentType: 'pdf' | 'video';
    pageNumber?: number;
    startTime?: number;
    endTime?: number;
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

    const iframeRef = useRef<HTMLIFrameElement>(null);
    const videoRef = useRef<HTMLVideoElement>(null);
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

            const response = await fetch(`/api/files/${fileId}/explanations`, {
                method: 'GET',
                headers: { Authorization: `Bearer ${idToken}` },
                mode: 'cors',
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
                throw new Error(errorData.detail || `Failed to fetch history: ${response.statusText}`);
            }

            const data: Explanation[] = await response.json();
            if (!Array.isArray(data)) {
                throw new Error('API returned malformed data');
            }

            setExplanationHistory(data.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()));
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
        if (showExplainPanel && user && fileId) {
            fetchExplanationHistory();
        }
        // Clear history if the panel is closed or file changes
        if (!showExplainPanel) {
            // Optionally clear history when panel closes, or keep it cached in state
            // setExplanationHistory([]);
        }
    }, [showExplainPanel, user, fileId, fetchExplanationHistory]);

    const createExplanation = async (variables: ExplainMutationVariables) => {
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

            const apiPayload: ExplainApiPayload = {
                ...variables,
                fileId: fileId,
            };

            const response = await fetch(`/api/files/${fileId}/explain`, {
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

    const handleExplainClick = () => {
        if (!fileId || !fileType || (fileType !== 'pdf' && fileType !== 'video')) {
            const msg = "Cannot explain: File type not supported or missing.";
            setError(msg);
            onError(msg);
            return;
        }

        let variables: ExplainMutationVariables = {
            contentType: fileType as 'pdf' | 'video',
        };

        if (fileType === 'pdf') {
            console.log("Explaining PDF page:", currentPage);
            variables.pageNumber = currentPage;
        } else { // fileType === 'video'
            const currentTime = videoRef.current?.currentTime;
            if (currentTime !== undefined) {
                const startTime = Math.floor(currentTime);
                console.log(`Explaining video at: ${startTime}s`);
                variables.startTime = startTime;
                variables.endTime = startTime + 1;
            } else {
                const msg = "Could not get video current time.";
                setError(msg);
                onError(msg);
                return;
            }
        }

        // Call the create function directly
        createExplanation(variables);
    };

    useEffect(() => {
        const type = getFileType(fileUrl || file.name);
        console.log(`Detected file type: ${type}`); // Debugging statement
        setFileType(type);
        if (!type) {
            onError(`Unsupported file type or could not determine type for: ${file.name}`);
        }
    }, [fileUrl, file.name, onError]);

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

        switch (fileType) {
            case 'pdf':
                return (
                    <div className="pdf-container file-content-area">
                        <iframe
                            ref={iframeRef}
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
                            ref={videoRef}
                            controls
                            src={fileUrl}
                            className="video-player"
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

            const response = await fetch('/api/files/explain', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    // Use the fetched idToken here
                    'Authorization': `Bearer ${idToken}`,
                },
                body: JSON.stringify({ file_id: file.id, selection: selectionText, selection_type: 'text' }),
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            console.log('Explanation received:', data);
            alert(`Explanation: ${data.explanation}`); // Placeholder display
        } catch (error) {
            console.error('Error explaining selection:', error);
            alert('Failed to get explanation.');
        }
    }, [selectionText, file, user]); // Update dependency array to include user object

    const handleCloseToolbox = useCallback(() => {
        setSelectionToolboxPosition(null); // Hide toolbox
        setSelectionText(''); // Clear selected text state
    }, []);

    useEffect(() => {
        const handleMouseUp = (event: MouseEvent) => {
            const selection = window.getSelection();
            const selectedText = selection?.toString().trim();

            // Check if selection exists and has text before proceeding
            if (selectedText && selection && iframeRef.current?.contains(event.target as Node)) { 
                const range = selection.getRangeAt(0); 
                const rect = range.getBoundingClientRect();
                const containerRect = iframeRef.current.getBoundingClientRect();
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

        const iframeElement = iframeRef.current;
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
