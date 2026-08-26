/**
 * The documents SyntextAI has written for this workspace.
 *
 * A list, and only a list. Writing one is done in the composer, in Write mode,
 * beside Ask and Find: this panel used to carry a second textarea and a second
 * submit button for the same kind of act, which was two places to type the same
 * shape of request.
 *
 * These are deliberately NOT in the knowledge base. A document here answers no
 * questions until somebody opens it, reads it and approves it, and the list
 * says so rather than leaving people to work it out.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Sparkles, FileText, Library } from 'lucide-react';
import { useUserContext } from '../UserContext';
import './DraftsPanel.css';

// Matches the API's own default page size.
const PAGE_SIZE = 20;

interface DraftSummary {
    id: number;
    title: string;
    status: 'draft' | 'ingested';
    ingested_file_id: number | null;
    created_at: string | null;
}

interface DraftsPanelProps {
    workspaceId: number | null;
    onOpenDraft: (draftId: number) => void;
    /** Switches the composer to Write mode. The panel offers the action and the
     *  composer performs it, so there is still one place to type. */
    onWrite: () => void;
    /** Bump to reload, e.g. after a document is written or approved. */
    refreshKey?: number;
}

const DraftsPanel: React.FC<DraftsPanelProps> = ({
    workspaceId, onOpenDraft, onWrite, refreshKey = 0,
}) => {
    const { user, orgContext } = useUserContext();
    const canManageDocuments = orgContext ? orgContext.can_manage_documents : true;

    const [drafts, setDrafts] = useState<DraftSummary[]>([]);
    const [total, setTotal] = useState(0);
    // The API pages at 20. Showing only the first page and no way to the rest
    // meant the twenty-first document a customer wrote simply vanished.
    const [limit, setLimit] = useState(PAGE_SIZE);
    const [open, setOpen] = useState(false);

    const load = useCallback(async () => {
        if (!user || workspaceId === null) { setDrafts([]); setTotal(0); return; }
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(
                `/api/v1/drafts?workspace_id=${workspaceId}&page=1&page_size=${limit}`, {
                headers: { 'Authorization': `Bearer ${idToken}` },
            });
            if (!res.ok) { setDrafts([]); setTotal(0); return; }
            const data = await res.json();
            setDrafts(data.items || []);
            setTotal(data.total ?? (data.items || []).length);
        } catch {
            setDrafts([]);
            setTotal(0);
        }
    }, [user, workspaceId, limit]);

    useEffect(() => { load(); }, [load, refreshKey]);

    // Reload when the panel is opened rather than only on mount. The list used
    // to be fetched once and never again, so a document written in another tab,
    // or approved from the document view, stayed invisible until the whole page
    // was reloaded.
    useEffect(() => { if (open) load(); }, [open, load]);

    // And when this tab comes back to the front, for the same reason.
    useEffect(() => {
        const onFocus = () => { if (!document.hidden) load(); };
        document.addEventListener('visibilitychange', onFocus);
        window.addEventListener('focus', onFocus);
        return () => {
            document.removeEventListener('visibilitychange', onFocus);
            window.removeEventListener('focus', onFocus);
        };
    }, [load]);

    // Nothing to show and nothing they may do: staff ask questions.
    if (!canManageDocuments && drafts.length === 0) return null;

    return (
        <div className="drafts-panel">
            <button
                type="button"
                className="drafts-panel-header"
                onClick={() => setOpen(o => !o)}
                aria-expanded={open}
            >
                <Sparkles className="size-4" />
                <span>Documents we wrote</span>
                {drafts.length > 0 && <span className="drafts-count">{drafts.length}</span>}
            </button>

            {open && (
                <div className="drafts-panel-body">
                    {canManageDocuments && (
                        <button type="button" className="drafts-write-cta" onClick={onWrite}>
                            <Sparkles className="size-3.5" /> Write a document
                        </button>
                    )}

                    {drafts.length === 0 ? (
                        <p className="drafts-empty">
                            Nothing yet. Written documents appear here, and answer no
                            questions until you add them to the knowledge base.
                        </p>
                    ) : (
                        <ul className="drafts-list">
                            {drafts.map(d => (
                                <li key={d.id}>
                                    <button type="button" onClick={() => onOpenDraft(d.id)}>
                                        <FileText className="size-3.5" />
                                        <span className="drafts-list-title">{d.title}</span>
                                        {/* Both fields, matching the server: deleting
                                            the approved document clears the id while
                                            status stays 'ingested'. */}
                                        {d.status === 'ingested' && d.ingested_file_id !== null
                                            ? <span className="drafts-badge in-kb" title="In the knowledge base">
                                                  <Library className="size-3" />
                                              </span>
                                            : <span className="drafts-badge">Draft</span>}
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}

                    {drafts.length < total && (
                        <button
                            type="button"
                            className="drafts-more"
                            onClick={() => setLimit(l => l + PAGE_SIZE)}
                        >
                            Show {Math.min(PAGE_SIZE, total - drafts.length)} more of {total}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};

export default DraftsPanel;
