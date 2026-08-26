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

interface DraftSummary {
    id: number;
    title: string;
    status: 'draft' | 'ingested';
    created_at: string | null;
}

interface DraftsPanelProps {
    workspaceId: number | null;
    canManageDocuments: boolean;
    onOpenDraft: (draftId: number) => void;
    /** Bump to force a reload, e.g. after a draft is approved elsewhere. */
    refreshKey?: number;
}

const DraftsPanel: React.FC<DraftsPanelProps> = ({
    workspaceId, canManageDocuments, onOpenDraft, refreshKey = 0,
}) => {
    const { user } = useUserContext();
    const { addToast } = useToast();

    const [drafts, setDrafts] = useState<DraftSummary[]>([]);
    const [prompt, setPrompt] = useState('');
    const [generating, setGenerating] = useState(false);
    const [elapsed, setElapsed] = useState(0);
    const [open, setOpen] = useState(false);
    const timer = useRef<ReturnType<typeof setInterval> | null>(null);

    const load = useCallback(async () => {
        if (!user || workspaceId === null) { setDrafts([]); return; }
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/drafts?workspace_id=${workspaceId}`, {
                headers: { 'Authorization': `Bearer ${idToken}` },
            });
            if (!res.ok) { setDrafts([]); return; }
            const data = await res.json();
            setDrafts(data.items || []);
        } catch {
            setDrafts([]);
        }
    }, [user, workspaceId]);

    useEffect(() => { load(); }, [load, refreshKey]);

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
                body: JSON.stringify({ workspace_id: workspaceId, prompt: prompt.trim() }),
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
                            <p className="drafts-hint">
                                Written from this workspace's documents only, and it answers no
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
                </div>
            )}
        </div>
    );
};

export default DraftsPanel;
