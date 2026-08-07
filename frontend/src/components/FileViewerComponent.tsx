import React, { useState, useEffect, useRef } from 'react';
import './FileViewerComponent.css';
import { UploadedFile, ProcessingStatus } from './types';
import { useUserContext } from '../UserContext';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
} from '@/components/ui/dialog';

interface FileViewerComponentProps {
    file: UploadedFile;
    /**
     * Location fragment to open the file at, e.g. "#page=36" from a citation or
     * "#t=12.5" for media. Chrome's built-in PDF viewer honours #page=N on the
     * iframe src, which is what makes a citation land on the cited page instead
     * of page 1.
     */
    fragment?: string;
    onClose: () => void;
    onError: (error: string) => void;
    darkMode: boolean;
}

const FileViewerComponent: React.FC<FileViewerComponentProps> = ({ file, fragment = '', onClose, onError, darkMode }) => {
    const [fileType, setFileType] = useState<string>('unknown');
    const [pages, setPages] = useState<{ page_number: number | null; content: string }[] | null>(null);
    const [pagesError, setPagesError] = useState<string | null>(null);
    // The token is fetched here rather than passed down: ChatApp's
    // callApiWithToken is a closure over its own state and is not exported.
    const { user } = useUserContext();

    const pdfViewerRef = useRef<HTMLIFrameElement>(null);
    const videoPlayerRef = useRef<HTMLVideoElement>(null);

    const fileUrl = file.file_url;
    const pdfIframeSrc = fragment ? `${fileUrl}${fragment}` : fileUrl;

    const getFileType = (urlOrName: string): string | null => {
        // Strip any query/fragment first: "p15.pdf#page=36".split('.').pop()
        // is "pdf#page=36", which matches no known type and sends the viewer
        // down the "unsupported file type" path. Signed URLs carry a query
        // string too, for the same reason.
        const bare = urlOrName.split(/[?#]/)[0];
        const extension = bare.split('.').pop()?.toLowerCase();
        if (!extension) {
            return null;
        }

        if (extension === 'pdf') return 'pdf';
        if (['mp4', 'webm', 'ogg', 'mov'].includes(extension)) return 'video';
        if (['jpg', 'jpeg', 'png', 'gif'].includes(extension)) return 'image';
        // No browser renders a .docx, so these are shown from the text we
        // extracted rather than from the original file. The uploader accepts
        // them, the processors handle them, and answers cite them, so falling
        // through to "unsupported" left a document searchable and unreadable at
        // the same time.
        if (['docx', 'doc', 'txt', 'md'].includes(extension)) return 'extracted';

        return null;
    };

    const STATUS_MESSAGES: Record<ProcessingStatus, string> = {
        uploaded: 'File has been uploaded and is queued for processing.',
        extracting: 'Extracting content from the file...',
        embedding: 'Generating embeddings for semantic search...',
        storing: 'Storing extracted content and metadata...',
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

    // The page a citation points at, from the same fragment the PDF viewer uses.
    const citedPage = (() => {
        const m = /[#&]page=(\d+)/.exec(fragment || '');
        return m ? parseInt(m[1], 10) : null;
    })();

    useEffect(() => {
        if (fileType !== 'extracted') return;
        let cancelled = false;
        (async () => {
            try {
                if (!user) throw new Error('not signed in');
                const token = await user.getIdToken();
                const res = await fetch(`api/v1/files/${file.id}/content`, {
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (!res.ok) throw new Error(`status ${res.status}`);
                const body = await res.json();
                if (!cancelled) setPages(body.pages || []);
            } catch (e) {
                if (!cancelled) setPagesError('Could not load this document\u2019s text.');
            }
        })();
        return () => { cancelled = true; };
    }, [fileType, file.id, user]);

    // Scroll the cited page into view once its text is on screen, which is what
    // #page=N does for a PDF and what a citation into a .docx needs to do too.
    useEffect(() => {
        if (!pages || !citedPage) return;
        const el = document.getElementById(`extracted-page-${citedPage}`);
        if (el) el.scrollIntoView({ block: 'start' });
    }, [pages, citedPage]);

    const renderFileContent = () => {
        if (!fileType) {
            return <div className="loading-indicator">Loading file...</div>;
        }

        if (file.status !== 'processed') {
            const statusMessage = STATUS_MESSAGES[file.status] || 'File is being processed...';
            return <div className="processing-indicator">{statusMessage}</div>;
        }

        if (fileType === 'pdf') {
            return (
                <div className="pdf-container file-content-area">
                    {/* key forces a remount when only the fragment changes:
                        assigning a new hash to an existing iframe src does not
                        renavigate, so clicking citation 1 then citation 3 would
                        otherwise leave the viewer on the first page it opened. */}
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
        } else if (fileType === 'extracted') {
            if (pagesError) {
                return <div className="error-message">{pagesError}</div>;
            }
            if (!pages) {
                return <div className="loading-indicator">Loading document…</div>;
            }
            if (pages.length === 0) {
                return <div className="processing-indicator">No text was extracted from this document.</div>;
            }
            return (
                <div className="extracted-container file-content-area">
                    {pages.map((p, i) => (
                        <section
                            key={p.page_number ?? i}
                            id={`extracted-page-${p.page_number ?? i + 1}`}
                            className={
                                citedPage && p.page_number === citedPage
                                    ? 'extracted-page extracted-page--cited'
                                    : 'extracted-page'
                            }
                        >
                            <div className="extracted-page__label">
                                Page {p.page_number ?? i + 1}
                            </div>
                            <pre className="extracted-page__text">{p.content}</pre>
                        </section>
                    ))}
                </div>
            );
        } else {
            return <div className="error-message">Unsupported file type: {fileType}</div>;
        }
    };

    // shadcn Dialog rather than a hand-rolled fixed overlay: it brings the
    // focus trap, Escape-to-close, scroll lock and aria wiring the previous
    // markup had none of. The document surface itself is unchanged, so the
    // #page=N anchor behaves exactly as before.
    return (
        <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
            <DialogContent
                // A document needs the screen. tailwind-merge resolves these
                // against DialogContent's own defaults (sm:max-w-sm, p-4,
                // gap-4, grid), so the sizing here wins without !important.
                className={`file-viewer-dialog flex flex-col w-[96vw] max-w-[96vw] sm:max-w-[1800px] h-[92vh] p-0 gap-0 overflow-hidden ${darkMode ? 'dark-mode' : ''}`}
            >
                <DialogHeader className="px-4 py-3 border-b shrink-0 pr-12">
                    <DialogTitle className="truncate text-left">{file.file_name}</DialogTitle>
                    <DialogDescription className="sr-only">
                        Document preview for {file.file_name}
                    </DialogDescription>
                </DialogHeader>

                <div className="file-viewer-main-layout">
                    <div className="document-view-container">
                        {renderFileContent()}
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
};

export default FileViewerComponent;
