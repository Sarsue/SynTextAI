import React, { useState, useEffect, useRef } from 'react';
import './FileViewerComponent.css';
import { UploadedFile } from './types';

interface FileViewerComponentProps {
    file: UploadedFile;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}

const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ file, onClose, onError, darkMode }) => {
    const fileUrl = file.publicUrl;
    const [fileType, setFileType] = useState<string | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [pageNumber, setPageNumber] = useState<number | null>(null);
    const [videoStartTime, setVideoStartTime] = useState<number | null>(null);
    
    // New states for explain feature
    const [showExplainPanel, setShowExplainPanel] = useState<boolean>(false);
    const [selectedContent, setSelectedContent] = useState<string>('');
    const [explanation, setExplanation] = useState<string>('');
    const [isExplaining, setIsExplaining] = useState<boolean>(false);
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [videoTimeRange, setVideoTimeRange] = useState<{start: number|null, end: number|null}>({ 
        start: null, 
        end: null 
    });
    
    // Refs for video and PDF iframe
    const videoRef = useRef<HTMLVideoElement>(null);
    const pdfFrameRef = useRef<HTMLIFrameElement>(null);
    
    // Listen for selection events from PDF iframe
    useEffect(() => {
        if (fileType === 'pdf') {
            // Setup message listener for PDF selections
            const handlePdfSelection = (event: MessageEvent) => {
                if (event.data && event.data.type === 'pdfSelection') {
                    setSelectedContent(event.data.text);
                    setCurrentPage(event.data.page || 1);
                }
            };
            
            window.addEventListener('message', handlePdfSelection);
            
            // Inject selection capture script into PDF iframe
            const injectSelectionScript = () => {
                if (pdfFrameRef.current && pdfFrameRef.current.contentWindow) {
                    try {
                        const script = `
                            document.addEventListener('mouseup', function() {
                                const selection = document.getSelection();
                                if (selection && selection.toString().trim()) {
                                    const pageElement = selection.anchorNode.parentElement.closest('.page');
                                    const pageNumber = pageElement ? parseInt(pageElement.dataset.pageNumber) : 1;
                                    window.parent.postMessage({
                                        type: 'pdfSelection',
                                        text: selection.toString(),
                                        page: pageNumber
                                    }, '*');
                                }
                            });
                        `;
                        
                        // Need to wait for PDF.js to load
                        setTimeout(() => {
                            const frameDoc = pdfFrameRef.current?.contentWindow?.document;
                            if (frameDoc) {
                                const scriptEl = frameDoc.createElement('script');
                                scriptEl.textContent = script;
                                frameDoc.body.appendChild(scriptEl);
                            }
                        }, 2000);
                    } catch (error) {
                        console.error('Error injecting selection script:', error);
                    }
                }
            };
            
            injectSelectionScript();
            
            return () => {
                window.removeEventListener('message', handlePdfSelection);
            };
        }
    }, [fileType]);
    
    // Handle explaining the selected content
    const handleExplain = async () => {
        if (!selectedContent && !videoTimeRange.start) return;
        
        setIsExplaining(true);
        
        try {
            const payload = {
                fileId: file.id,
                fileType,
                selectionType: fileType === 'pdf' ? 'text' : 'video_range',
                content: fileType === 'pdf' ? selectedContent : null,
                page: currentPage,
                videoStart: videoTimeRange.start,
                videoEnd: videoTimeRange.end || videoTimeRange.start,
            };
            
            const response = await fetch('/api/v1/files/explain', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });
            
            if (!response.ok) {
                throw new Error('Failed to get explanation');
            }
            
            const data = await response.json();
            setExplanation(data.explanation);
        } catch (error) {
            console.error('Error getting explanation:', error);
            onError('Could not generate explanation. Please try again.');
        } finally {
            setIsExplaining(false);
        }
    };
    
    // For video time range selection
    const handleSetVideoStart = () => {
        if (videoRef.current) {
            setVideoTimeRange({
                ...videoTimeRange,
                start: videoRef.current.currentTime
            });
        }
    };
    
    const handleSetVideoEnd = () => {
        if (videoRef.current) {
            setVideoTimeRange({
                ...videoTimeRange,
                end: videoRef.current.currentTime
            });
        }
    };

    useEffect(() => {
        // Extract page or time parameters if they exist
        try {
            const url = new URL(fileUrl);
            const pageParam = url.searchParams.get('page');
            if (pageParam) {
                setPageNumber(parseInt(pageParam, 10));
            }
            
            const timeParam = url.searchParams.get('time');
            if (timeParam) {
                setVideoStartTime(parseInt(timeParam, 10));
            }
        } catch (e) {
            console.log('Could not parse URL parameters');
        }
        
        const detectedFileType = getFileType(fileUrl);
        if (!detectedFileType) {
            console.log(`Unsupported file type for: ${fileUrl}`);
            onError('Unsupported file type');
            setLoading(false);
            return;
        }

        setFileType(detectedFileType);
        setLoading(false); // No fetching needed, we're done loading
    }, [fileUrl, onError]);

    // Re-add getFileType function (assuming it was removed)
    const getFileType = (fileUrl: string): string | null => {
        try {
            const urlObj = new URL(fileUrl.split('?')[0]); // Use only base URL for extension detection
            const pathname = urlObj.pathname;
            const extension = pathname.split('.').pop()?.toLowerCase();

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
                default:
                    return null;
            }
        } catch (error) {
            console.error("Error parsing URL in getFileType:", fileUrl, error);
            return null;
        }
    };

    // Enhanced renderFileContent with Explain features
    const renderFileContent = () => {
        if (loading) {
            return <div className="loading-indicator">Loading...</div>;
        }

        if (!fileType) {
            return <div className="error-message">Could not determine file type</div>;
        }

        switch (fileType) {
            case 'pdf':
                // Use a direct iframe approach with parameters to force viewing
                // The #view=FitH parameter helps trigger the browser's PDF viewer
                const pdfUrlWithViewParam = `${fileUrl}#view=FitH`;
                return (
                    <div className={`file-viewer-with-explain ${showExplainPanel ? 'with-panel' : ''}`}>
                        <div className="document-view">
                            <iframe
                                ref={pdfFrameRef}
                                src={pdfUrlWithViewParam}
                                width="100%"
                                height="750px"
                                style={{ border: 'none' }}
                                title="PDF Viewer"
                            ></iframe>
                            
                            {selectedContent && (
                                <div className="selection-tools">
                                    <button 
                                        onClick={() => {
                                            setShowExplainPanel(true);
                                            handleExplain();
                                        }}
                                        className="explain-button"
                                    >
                                        Explain Selection
                                    </button>
                                </div>
                            )}
                        </div>
                        
                        {showExplainPanel && (
                            <div className={`explain-panel ${darkMode ? 'dark-mode' : ''}`}>
                                <div className="panel-header">
                                    <h3>AI Explanation</h3>
                                    <button onClick={() => setShowExplainPanel(false)} className="close-panel-button">×</button>
                                </div>
                                <div className="panel-content">
                                    {isExplaining ? (
                                        <div className="loading-explanation">Generating explanation...</div>
                                    ) : (
                                        <>
                                            <div className="selection-preview">
                                                <strong>Selected Text:</strong>
                                                <p>{selectedContent}</p>
                                            </div>
                                            <div className="explanation-content">
                                                <strong>Explanation:</strong>
                                                <p>{explanation}</p>
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                );
            case 'image':
                return <img src={fileUrl} alt="File content" style={{ width: '100%', height: 'auto' }} />;
            case 'video':
                return (
                    <div className={`file-viewer-with-explain ${showExplainPanel ? 'with-panel' : ''}`}>
                        <div className="document-view">
                            <video 
                                ref={videoRef}
                                controls 
                                width="100%" 
                                height="auto"
                                {...(videoStartTime ? { currentTime: videoStartTime } : {})}
                            >
                                <source src={fileUrl} type="video/mp4" />
                                Your browser does not support the video tag or the video format.
                            </video>
                            
                            <div className="video-selection-tools">
                                <button onClick={handleSetVideoStart} className="set-time-button">
                                    Set Start ({videoTimeRange.start?.toFixed(1) || 'Not Set'})
                                </button>
                                <button onClick={handleSetVideoEnd} className="set-time-button">
                                    Set End ({videoTimeRange.end?.toFixed(1) || 'Not Set'})
                                </button>
                                {videoTimeRange.start !== null && (
                                    <button 
                                        onClick={() => {
                                            setShowExplainPanel(true);
                                            handleExplain();
                                        }}
                                        className="explain-button"
                                    >
                                        Explain This Clip
                                    </button>
                                )}
                            </div>
                        </div>
                        
                        {showExplainPanel && (
                            <div className={`explain-panel ${darkMode ? 'dark-mode' : ''}`}>
                                <div className="panel-header">
                                    <h3>AI Explanation</h3>
                                    <button onClick={() => setShowExplainPanel(false)} className="close-panel-button">×</button>
                                </div>
                                <div className="panel-content">
                                    {isExplaining ? (
                                        <div className="loading-explanation">Generating explanation...</div>
                                    ) : (
                                        <>
                                            <div className="selection-preview">
                                                <strong>Selected Time Range:</strong>
                                                <p>
                                                    {videoTimeRange.start?.toFixed(1)}s 
                                                    {videoTimeRange.end ? ` to ${videoTimeRange.end.toFixed(1)}s` : ''}
                                                </p>
                                            </div>
                                            <div className="explanation-content">
                                                <strong>Explanation:</strong>
                                                <p>{explanation}</p>
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                );
            default:
                return <div>Unsupported file type</div>;
        }
    };

    const handleClose = () => {
        onClose();
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                {renderFileContent()}
                {/* Consider making the close button more accessible */}
                <button onClick={handleClose} className="close-button" aria-label="Close file viewer">❌</button>
            </div>
        </div>
    );
};

export default FileViewerComponent;
