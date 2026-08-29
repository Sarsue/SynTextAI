import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import FileViewerComponent from './FileViewerComponent';
import { ProcessingStatus, UploadedFile } from './types';
import { useUserContext } from '../UserContext';

/**
 * One document, opened at one page, from an address.
 *
 * WHY THIS EXISTS
 *
 * The viewer has always been a dialog inside /chat, opened by React state from
 * a citation click. That meant "this document at this page" had no URL: it
 * could not be bookmarked, sent to a colleague, or linked to from anywhere
 * outside the app. Nobody noticed, because the only way to reach a citation was
 * to click one in an answer already on screen.
 *
 * MCP is what made it matter, but it is not the reason this is general. A
 * citation with an address is the primitive; MCP is only its first consumer.
 * Teams, WhatsApp, a browser extension and an emailed answer all need the same
 * thing and can all use this identical URL. Anywhere a citation is text rather
 * than a click, this is the way back to the source page.
 *
 * Its own route rather than a query parameter on /chat, because opening a
 * document is not a conversation. A link that resumes somebody's last chat on
 * the way to page 62 is a different thing from a link to page 62.
 *
 * The viewer, the signing and the authorization are all reused untouched. This
 * file adds an address and nothing else.
 */

/** The viewer decides what to render from the URL, but the type is required. */
const typeFromName = (name: string): UploadedFile['file_type'] => {
    const ext = name.split(/[?#]/)[0].split('.').pop()?.toLowerCase() || '';
    if (ext === 'pdf') return 'pdf';
    if (['jpg', 'jpeg', 'png', 'gif'].includes(ext)) return 'image';
    if (['mp4', 'webm', 'ogg', 'mov'].includes(ext)) return 'video';
    if (['mp3', 'wav', 'm4a'].includes(ext)) return 'audio';
    return 'text';
};

const DocumentPage: React.FC = () => {
    const { fileId, page } = useParams<{ fileId: string; page?: string }>();
    const navigate = useNavigate();
    const { user, darkMode } = useUserContext();

    const [file, setFile] = useState<UploadedFile | null>(null);
    const [error, setError] = useState<string | null>(null);

    const numericId = Number(fileId);
    const pageNumber = Number(page);
    const hasPage = Number.isInteger(pageNumber) && pageNumber > 0;

    useEffect(() => {
        if (!Number.isInteger(numericId) || numericId <= 0) {
            setError('That link does not point at a document.');
            return;
        }
        // requireUser holds this route, so a null user here is the moment before
        // Firebase reports in rather than a signed-out reader.
        if (!user) return;

        let cancelled = false;
        (async () => {
            try {
                const token = await user.getIdToken();
                // Same endpoint the citation click uses. Authorization happens
                // there, per request, against the document's workspace, so a
                // link that outlives someone's access stops working on its own.
                const response = await fetch(`api/v1/files/${numericId}/access-url`, {
                    headers: { Authorization: `Bearer ${token}` },
                    mode: 'cors',
                    credentials: 'include',
                });
                if (!response.ok) {
                    if (!cancelled) {
                        // 403 and 404 get the same sentence on purpose. Telling
                        // them apart would tell a stranger holding a guessed id
                        // whether the document exists.
                        setError(
                            'That document is not available. It may have been removed, '
                            + 'or you may not have access to it.'
                        );
                    }
                    return;
                }
                const body = await response.json();
                if (cancelled) return;
                const name = body.file_name || 'Document';
                setFile({
                    id: numericId,
                    file_name: name,
                    file_url: body.url,
                    file_type: body.file_type || typeFromName(name),
                    status: (body.status || 'processed') as ProcessingStatus,
                    superseded_by_id: body.superseded_by_id ?? null,
                });
            } catch {
                if (!cancelled) setError('Could not open this document.');
            }
        })();
        return () => { cancelled = true; };
    }, [numericId, user]);

    // Closing the viewer has to go somewhere, and there is no page underneath
    // this one to go back to.
    const leave = () => navigate('/chat');

    if (error) {
        return (
            <div className="auth-page">
                <div className="error-message">{error}</div>
                <button className="link-button" onClick={leave}>Back to SyntextAI</button>
            </div>
        );
    }

    if (!file) {
        return (
            <div className="auth-page">
                <span className="auth-loading">Opening document...</span>
            </div>
        );
    }

    return (
        <FileViewerComponent
            file={file}
            fragment={hasPage ? `#page=${pageNumber}` : ''}
            onClose={leave}
            onError={setError}
            darkMode={darkMode}
        />
    );
};

export default DocumentPage;
