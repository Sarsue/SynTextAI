import React, { useState, useEffect, useRef } from 'react';
import './FileViewerComponent.css';
import { UploadedFile } from './types';
import { useUserContext } from '../UserContext';

interface FileViewerComponentProps {
  file: UploadedFile;
  onClose: () => void;
  onError: (error: string) => void;
  darkMode: boolean;
}

const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ file, onClose, onError, darkMode }) => {
  const { user } = useUserContext();
  const fileUrl = file.publicUrl;

  const [fileType, setFileType] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [videoStartTime, setVideoStartTime] = useState<number | null>(null);
  const [selectedContent, setSelectedContent] = useState('');
  const [explanation, setExplanation] = useState('');
  const [isExplaining, setIsExplaining] = useState(false);
  const [showExplainPanel, setShowExplainPanel] = useState(false);
  const [videoTimeRange, setVideoTimeRange] = useState<{ start: number | null; end: number | null }>({ start: null, end: null });

  const videoRef = useRef<HTMLVideoElement>(null);
  const pdfFrameRef = useRef<HTMLIFrameElement>(null);
  const pdfContainerRef = useRef<HTMLDivElement>(null);

  const hasPremiumAccess = true; // TODO: Replace with real subscription check

  const getFileType = (url: string): string | null => {
    try {
      const path = new URL(url).pathname.toLowerCase();
      if (path.endsWith('.pdf')) return 'pdf';
      if (/\.(jpe?g|png|gif|webp)$/.test(path)) return 'image';
      if (/\.(mp4|webm|mov)$/.test(path)) return 'video';
    } catch (err) {
      console.error('Invalid URL:', err);
    }
    return null;
  };

  useEffect(() => {
    if (!fileUrl) {
      onError('No file URL provided');
      setLoading(false);
      return;
    }

    const type = getFileType(fileUrl);
    setFileType(type);

    if (!type) {
      onError('Unsupported file type');
      setLoading(false);
      return;
    }

    const url = new URL(fileUrl);
    const page = url.searchParams.get('page');
    const time = url.searchParams.get('time');

    if (page) setCurrentPage(parseInt(page));
    if (time) setVideoStartTime(parseInt(time));

    setSelectedContent('');
    setExplanation('');
    setIsExplaining(false);
    setShowExplainPanel(false);
    setVideoTimeRange({ start: null, end: null });

    const timer = setTimeout(() => setLoading(false), 1000);
    return () => clearTimeout(timer);
  }, [fileUrl, onError]);

  useEffect(() => {
    if (fileType !== 'pdf' || !pdfContainerRef.current) return;

    const handleMouseUp = () => {
      const selectedText = window.getSelection()?.toString().trim();
      if (selectedText) {
        setSelectedContent(selectedText);
        setCurrentPage(currentPage); // fallback
      }
    };

    const container = pdfContainerRef.current;
    container.addEventListener('mouseup', handleMouseUp);

    return () => container.removeEventListener('mouseup', handleMouseUp);
  }, [fileType]);

  useEffect(() => {
    if (fileType !== 'pdf') return;

    const handlePdfSelection = (event: MessageEvent) => {
      if (event.data?.type === 'pdfSelection') {
        setSelectedContent(event.data.text);
        setCurrentPage(event.data.page || 1);
      }
    };

    window.addEventListener('message', handlePdfSelection);
    return () => window.removeEventListener('message', handlePdfSelection);
  }, [fileType]);

  const injectSelectionScript = () => {
    const frame = pdfFrameRef.current;
    const doc = frame?.contentDocument || frame?.contentWindow?.document;
    if (!doc) return;

    const existing = doc.getElementById('pdfSelectionScript');
    if (existing) return;

    const script = doc.createElement('script');
    script.id = 'pdfSelectionScript';
    script.textContent = `
      document.addEventListener('mouseup', () => {
        const text = window.getSelection()?.toString().trim();
        const page = document.querySelector('.page[data-page-number]');
        const pageNumber = page ? parseInt(page.getAttribute('data-page-number')) : 1;
        if (text) {
          window.parent.postMessage({ type: 'pdfSelection', text, page: pageNumber }, '*');
        }
      });
    `;
    doc.body.appendChild(script);
  };

  const handleExplain = async () => {
    if (!user) {
      onError('Please log in to use the explain feature');
      return;
    }

    if (fileType === 'pdf' && !selectedContent) {
      setSelectedContent(`Content from page ${currentPage}`);
    }

    if (fileType === 'video') {
      const { start, end } = videoTimeRange;
      if (start == null || end == null) {
        onError('Please set both start and end times for the video clip');
        return;
      }
    }

    setIsExplaining(true);
    try {
      // TODO: Replace with real explanation API logic
      const fakeExplanation = `Explanation for "${selectedContent || `video time ${videoTimeRange.start} - ${videoTimeRange.end}`}"`;
      await new Promise(res => setTimeout(res, 1000)); // Simulate API call
      setExplanation(fakeExplanation);
      setShowExplainPanel(true);
    } catch (err) {
      onError('Failed to explain content');
    } finally {
      setIsExplaining(false);
    }
  };
    // Handle closing the viewer
    const handleClose = () => {
        onClose();
    };

    // Render file content based on type
    const renderFileContent = () => {
        if (loading) {
            return <div className="loading-indicator">Loading...</div>;
        }

        if (!fileType) {
            return <div className="error-message">Could not determine file type</div>;
        }

        switch (fileType) {
            case 'pdf':
                return (
                    <div ref={pdfContainerRef} className="pdf-container">
                        {/* PDF Viewer */}
                        <iframe
                            ref={pdfFrameRef}
                            src={fileUrl}
                            className="pdf-viewer"
                            onLoad={() => {
                                setLoading(false);
                                injectSelectionScript();
                            }}
                            title="PDF Viewer"
                        />

                        {/* PDF Controls */}
                        <div className="pdf-controls">
                            {/* Debug button - only in development */}
                            {process.env.NODE_ENV === 'development' && (
                                <div className="debug-selections">
                                    <button onClick={() => console.log('Current selection:', selectedContent)}>
                                        Debug Selection
                                    </button>
                                </div>
                            )}

                            {/* Explain tools for PDF */}
                            {hasPremiumAccess && (
                                <div className="selection-tools">
                                    <button
                                        onClick={() => {
                                            // Toggle explanation panel
                                            setShowExplainPanel(!showExplainPanel);
                                            // If panel is being opened, generate explanation
                                            if (!showExplainPanel) {
                                                handleExplain();
                                            }
                                        }}
                                        className="explain-button"
                                        title="Get AI Explanation"
                                    >
                                        🧠 Explain Selection
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                );

            case 'video':
                return (
                    <div className="video-container">
                        <video
                            ref={videoRef}
                            controls
                            className="video-player"
                            onLoadedData={() => setLoading(false)}
                            {...(videoStartTime ? { currentTime: videoStartTime } : {})}
                        >
                            <source src={fileUrl} type="video/mp4" />
                            Your browser does not support the video tag or the video format.
                        </video>

                        {/* Video time selection controls */}
                        {hasPremiumAccess && (
                            <div className="video-selection-tools">
                                <button 
                                    onClick={() => {
                                        const time = videoRef.current?.currentTime || null;
                                        setVideoTimeRange({ start: time, end: null });
                                    }} 
                                    className="set-time-button"
                                >
                                    Set Start
                                </button>
                                <button 
                                    onClick={() => {
                                        const time = videoRef.current?.currentTime || null;
                                        setVideoTimeRange({ ...videoTimeRange, end: time });
                                    }} 
                                    className="set-time-button"
                                >
                                    Set End
                                </button>
                                <button
                                    onClick={() => {
                                        // Toggle the explanation panel
                                        setShowExplainPanel(!showExplainPanel);
                                        // If panel is being opened, generate explanation
                                        if (!showExplainPanel) {
                                            handleExplain();
                                        }
                                    }}
                                    className="explain-button"
                                    title="Get AI Explanation"
                                >
                                    🧠 Explain Clip
                                </button>
                            </div>
                        )}
                    </div>
                );

            case 'image':
                return (
                    <div className="image-container">
                        <img src={fileUrl} alt={file.name} className="image-viewer" onLoad={() => setLoading(false)} />
                    </div>
                );

            default:
                return <div className="error-message">Unsupported file type</div>;
        }
    };

    return (
        <div className={`file-viewer-modal ${darkMode ? 'dark-mode' : ''}`}>
            <div className="file-viewer-content">
                <div className="document-view">
                    {renderFileContent()}
                </div>

                {/* Explain panel */}
                {showExplainPanel && (
                    <div className="explain-panel">
                        <div className="panel-header">
                            <h3>AI Explanation</h3>
                            <div className="panel-controls">
                                <button 
                                    onClick={handleExplain} 
                                    className="explain-button" 
                                    disabled={isExplaining}
                                    title={isExplaining ? 'Generating...' : 'Generate new explanation'}
                                >
                                    {isExplaining ? '🔄' : '🧠'}
                                </button>
                                <button 
                                    onClick={() => setShowExplainPanel(false)} 
                                    className="close-panel-button"
                                    title="Close panel"
                                >
                                    ×
                                </button>
                            </div>
                        </div>
                        <div className="panel-content">
                            {isExplaining ? (
                                <div className="loading-explanation">
                                    <div className="spinner">🔄</div>
                                    <p>AI is analyzing your content...</p>
                                </div>
                            ) : (
                                <>
                                    <div className="selection-preview">
                                        <h4>{fileType === 'video' ? 'Selected Clip' : 'Selected Content'}</h4>
                                        {fileType === 'video' ? (
                                            <div className="time-range">
                                                <span className="time-label">Start:</span>
                                                <span className="time-value">
                                                    {videoTimeRange.start !== null 
                                                        ? `${videoTimeRange.start.toFixed(1)}s` 
                                                        : 'Not set'}
                                                </span>
                                                <span className="time-label">End:</span>
                                                <span className="time-value">
                                                    {videoTimeRange.end !== null 
                                                        ? `${videoTimeRange.end.toFixed(1)}s` 
                                                        : 'Not set'}
                                                </span>
                                            </div>
                                        ) : (
                                            <div className="text-preview">
                                                <p className="selection">{selectedContent || `Content from page ${currentPage}`}</p>
                                                {currentPage && <span className="page-number">Page {currentPage}</span>}
                                            </div>
                                        )}
                                    </div>
                                    <div className="explanation-content">
                                        <h4>AI Explanation</h4>
                                        {explanation ? (
                                            <div className="explanation">{explanation}</div>
                                        ) : (
                                            <div className="no-explanation">
                                                <p>No explanation yet</p>
                                                <button 
                                                    onClick={handleExplain}
                                                    className="generate-button"
                                                    disabled={isExplaining}
                                                >
                                                    🧠 Generate Explanation
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                )}

                {/* File viewer controls */}
                <div className="file-viewer-controls">
                    {/* Explain button - only shown for premium users */}
                    {hasPremiumAccess && (
                        <button 
                            onClick={() => {
                                if (fileType === 'pdf' && !selectedContent) {
                                    setSelectedContent(`Content from page ${currentPage}`);
                                }
                                setShowExplainPanel(!showExplainPanel);
                                if (!showExplainPanel) {
                                    handleExplain();
                                }
                            }} 
                            className="action-button explain-button" 
                            aria-label="Explain with AI"
                            title="Explain with AI"
                            style={{ display: hasPremiumAccess ? 'block' : 'none' }}
                        >
                            🧠 Explain
                        </button>
                    )}
                    <button 
                        onClick={handleClose} 
                        className="action-button close-button" 
                        aria-label="Close file viewer"
                    >
                        ❌
                    </button>
                </div>
            </div>
        </div>
    );
}

export default FileViewerComponent;
