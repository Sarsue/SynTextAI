/**
 * Ask SyntextAI to write a document, and the list of what it has written.
 *
 * Sits under the knowledge base because that is where documents live, but these
 * are deliberately NOT in it: a draft answers no questions until somebody opens
 * it, reads it and presses "Add to knowledge base". The list says so rather than
 * leaving people to work it out.
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Sparkles, Loader2, FileText, Library } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';
import './DraftsPanel.css';

// Matches the API's own default page size.
const PAGE_SIZE = 20;

interface DraftSummary {
    id: number;
    title: string;
    status: 'draft' | 'ingested';
    created_at: string | null;
}

interface DraftsPanelProps {
    workspaceId: number | null;
    canManageDocuments: boolean;
    /** The conversation on screen, if any. Offered as a source for the
     *  document, never used silently: a chat about something unrelated is a
     *  surprising way to get a wrong document. */
    historyId?: number | null;
    onOpenDraft: (draftId: number) => void;
    /** Bump to force a reload, e.g. after a draft is approved elsewhere. */
    refreshKey?: number;
}

const DraftsPanel: React.FC<DraftsPanelProps> = ({
    workspaceId, canManageDocuments, historyId = null, onOpenDraft, refreshKey = 0,
}) => {
    const { user } = useUserContext();
    const { addToast } = useToast();

    const [drafts, setDrafts] = useState<DraftSummary[]>([]);
    const [total, setTotal] = useState(0);
    // The API pages at 20. Showing only the first page and no way to the rest
    // meant the twenty-first document a customer wrote simply vanished.
    const [limit, setLimit] = useState(PAGE_SIZE);
    const [prompt, setPrompt] = useState('');
    const [generating, setGenerating] = useState(false);
    const [elapsed, setElapsed] = useState(0);
    const [open, setOpen] = useState(false);
    const [useConversation, setUseConversation] = useState(false);
    const timer = useRef<ReturnType<typeof setInterval> | null>(null);

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
    // to be fetched once and never again, so a draft written in another tab, or
    // one approved from the document view, stayed invisible until the whole
    // page was reloaded.
    useEffect(() => { if (open) load(); }, [open, load]);

    // Clear the choice when the conversation goes. Leaving it ticked would send
    // a history_id belonging to a chat the person has already left.
    useEffect(() => { if (historyId === null) setUseConversation(false); }, [historyId]);

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

    // Writing a document runs a retrieval and a long generation, so it takes
    // meaningfully longer than a chat answer. Saying how long it has been
    // running is the difference between waiting and wondering if it broke.
    useEffect(() => {
        if (generating) {
            setElapsed(0);
            timer.current = setInterval(() => setElapsed(e => e + 1), 1000);
        } else if (timer.current) {
            clearInterval(timer.current);
            timer.current = null;
        }
        return () => { if (timer.current) clearInterval(timer.current); };
    }, [generating]);

    const handleGenerate = async () => {
        if (!user || workspaceId === null || !prompt.trim()) return;
        setGenerating(true);
        try {
            const idToken = await user.getIdToken();
            const res = await fetch('/api/v1/drafts/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
                body: JSON.stringify({
                    workspace_id: workspaceId,
                    prompt: prompt.trim(),
                    // Only when asked for. Folding the conversation in by
                    // default would let an unrelated chat quietly steer a
                    // document somebody is about to hand to staff.
                    ...(useConversation && historyId !== null
                        ? { history_id: historyId }
                        : {}),
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                addToast(typeof data.detail === 'string' ? data.detail : 'Could not write that', 'error');
                return;
            }
            setPrompt('');
            await load();
            onOpenDraft(data.id);
        } catch {
            addToast('Could not write that', 'error');
        } finally {
            setGenerating(false);
        }
    };

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
                <span>Write a document</span>
                {drafts.length > 0 && <span className="drafts-count">{drafts.length}</span>}
            </button>

            {open && (
                <div className="drafts-panel-body">
                    {canManageDocuments && (
                        <>
                            <textarea
                                className="drafts-prompt"
                                value={prompt}
                                onChange={e => setPrompt(e.target.value)}
                                placeholder="An onboarding checklist for a new hygienist, from our policies"
                                rows={3}
                                disabled={generating || workspaceId === null}
                            />
                            <Button
                                size="sm"
                                className="drafts-generate-button"
                                onClick={handleGenerate}
                                disabled={generating || !prompt.trim() || workspaceId === null}
                            >
                                {generating
                                    ? <><Loader2 className="size-3.5 animate-spin" /> Writing, {elapsed}s</>
                                    : <><Sparkles className="size-3.5" /> Write it</>}
                            </Button>
                            {historyId !== null && (
                                <label className="drafts-use-conversation">
                                    <input
                                        type="checkbox"
                                        checked={useConversation}
                                        onChange={e => setUseConversation(e.target.checked)}
                                        disabled={generating}
                                    />
                                    Use this conversation too
                                </label>
                            )}
                            <p className="drafts-hint">
                                Written from this workspace's documents{useConversation && historyId !== null
                                    ? ' and the conversation on screen'
                                    : ' only'}, and it answers no
                                questions until you add it to the knowledge base.
                            </p>
                        </>
                    )}

                    {drafts.length > 0 && (
                        <ul className="drafts-list">
                            {drafts.map(d => (
                                <li key={d.id}>
                                    <button type="button" onClick={() => onOpenDraft(d.id)}>
                                        <FileText className="size-3.5" />
                                        <span className="drafts-list-title">{d.title}</span>
                                        {d.status === 'ingested'
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
                            Show {Math.min(PAGE_SIZE, total - drafts.length)} more
                            of {total}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};

export default DraftsPanel;
