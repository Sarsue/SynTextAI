/**
 * A document SyntextAI wrote, open for a person to edit and then approve.
 *
 * Two things about this screen are load-bearing rather than decorative.
 *
 * It says a machine wrote it, at the top, every time, and it keeps saying so
 * after the text has been edited. A document that looks like it came from the
 * business is the failure mode: somebody prints it, hands it to staff, and the
 * one wrong figure in it is now policy.
 *
 * And "Add to knowledge base" is a separate, deliberate action rather than
 * something that happens on save. Until it is pressed this document cannot
 * answer a single question, which is enforced in the database rather than here:
 * drafts live in their own table and retrieval never joins it. See
 * api/routes/drafts.py.
 */
import React, { useState, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Sparkles, Save, Library, Trash2, X, Loader2, FileText, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';
import ConfirmDialog from './ConfirmDialog';
import './DraftView.css';


/** The name the server chose, or a usable fallback.
 *
 * The header is already stripped to safe characters server-side; this only has
 * to read it, and fall back when a proxy drops the header entirely. */
function filenameFrom(disposition: string | null, title: string, extension: string): string {
    const match = disposition?.match(/filename="([^"]+)"/);
    if (match) return match[1];
    const cleaned = (title || 'document').replace(/[^\w\s.-]/g, '').trim() || 'document';
    return `${cleaned}.${extension}`;
}

interface DraftSource {
    segment: number;
    file_id: number | null;
    file_name: string | null;
    page_number: number | null;
}

interface Draft {
    id: number;
    workspace_id: number;
    title: string;
    prompt: string;
    content: string;
    sources: DraftSource[];
    status: 'draft' | 'ingested';
    ingested_file_id: number | null;
    created_at: string | null;
}

interface DraftViewProps {
    draftId: number;
    onClose: () => void;
    /** So the document list refreshes once a draft becomes a real document. */
    onIngested?: () => void;
    onDeleted?: () => void;
}

const DraftView: React.FC<DraftViewProps> = ({ draftId, onClose, onIngested, onDeleted }) => {
    const { user } = useUserContext();
    const { addToast } = useToast();

    const [draft, setDraft] = useState<Draft | null>(null);
    const [title, setTitle] = useState('');
    const [content, setContent] = useState('');
    const [mode, setMode] = useState<'edit' | 'preview'>('preview');
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [ingesting, setIngesting] = useState(false);
    const [confirmIngest, setConfirmIngest] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [exporting, setExporting] = useState<'docx' | 'pdf' | null>(null);

    // What the server last agreed the document says. Comparing against this is
    // how the Save button knows whether there is anything to save, so an
    // untouched draft cannot be "saved" into an identical revision.
    const [saved, setSaved] = useState({ title: '', content: '' });
    const dirty = title !== saved.title || content !== saved.content;

    const authFetch = useCallback(async (url: string, init: RequestInit = {}) => {
        const idToken = await user!.getIdToken();
        return fetch(url, {
            ...init,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${idToken}`,
                ...(init.headers || {}),
            },
        });
    }, [user]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            if (!user) return;
            setLoading(true);
            try {
                const res = await authFetch(`/api/v1/drafts/${draftId}`);
                if (!res.ok) throw new Error(String(res.status));
                const data: Draft = await res.json();
                if (cancelled) return;
                setDraft(data);
                setTitle(data.title);
                setContent(data.content);
                setSaved({ title: data.title, content: data.content });
            } catch {
                if (!cancelled) addToast('Could not open that document', 'error');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [draftId, user, authFetch, addToast]);

    const handleSave = async () => {
        if (!dirty) return;
        setSaving(true);
        try {
            const res = await authFetch(`/api/v1/drafts/${draftId}`, {
                method: 'PATCH',
                body: JSON.stringify({ title, content }),
            });
            if (!res.ok) {
                const data = await res.json();
                addToast(data.detail || 'Could not save', 'error');
                return;
            }
            setSaved({ title, content });
            addToast('Saved', 'success');
        } catch {
            addToast('Could not save', 'error');
        } finally {
            setSaving(false);
        }
    };

    const handleExport = async (format: 'docx' | 'pdf') => {
        setExporting(format);
        try {
            // Save first. Downloading the server's copy while the person is
            // looking at an edited one hands them a file that does not match
            // what is on their screen.
            if (dirty) {
                const saveRes = await authFetch(`/api/v1/drafts/${draftId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ title, content }),
                });
                if (!saveRes.ok) {
                    addToast('Could not save your edits, so nothing was downloaded', 'error');
                    return;
                }
                setSaved({ title, content });
            }

            const res = await authFetch(`/api/v1/drafts/${draftId}/export?format=${format}`);
            if (!res.ok) {
                addToast(`Could not build the ${format === 'pdf' ? 'PDF' : 'Word document'}`, 'error');
                return;
            }
            // The endpoint needs the auth header, so this cannot be a plain
            // link: fetch it, then hand the bytes to the browser.
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filenameFrom(res.headers.get('content-disposition'), title, format);
            document.body.appendChild(link);
            link.click();
            link.remove();
            // Revoked on the next tick rather than immediately: Safari has not
            // started reading the blob by the time click() returns.
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch {
            addToast(`Could not build the ${format === 'pdf' ? 'PDF' : 'Word document'}`, 'error');
        } finally {
            setExporting(null);
        }
    };

    const handleIngest = async () => {
        setConfirmIngest(false);
        setIngesting(true);
        try {
            // Save first. Approving the version on the server while the person
            // is looking at an edited one would put a document they never read
            // into the knowledge base.
            if (dirty) {
                const saveRes = await authFetch(`/api/v1/drafts/${draftId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ title, content }),
                });
                if (!saveRes.ok) {
                    addToast('Could not save your edits, so nothing was added', 'error');
                    return;
                }
                setSaved({ title, content });
            }

            const res = await authFetch(`/api/v1/drafts/${draftId}/ingest`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok) {
                addToast(data.detail || 'Could not add it to the knowledge base', 'error');
                return;
            }
            addToast('Added to the knowledge base. It can answer questions once processed.', 'success');
            setDraft(d => (d ? { ...d, status: 'ingested', ingested_file_id: data.file_id } : d));
            onIngested?.();
        } catch {
            addToast('Could not add it to the knowledge base', 'error');
        } finally {
            setIngesting(false);
        }
    };

    const handleDelete = async () => {
        setConfirmDelete(false);
        try {
            const res = await authFetch(`/api/v1/drafts/${draftId}`, { method: 'DELETE' });
            if (!res.ok) {
                addToast('Could not delete it', 'error');
                return;
            }
            addToast('Deleted', 'success');
            onDeleted?.();
            onClose();
        } catch {
            addToast('Could not delete it', 'error');
        }
    };

    if (loading) {
        return (
            <div className="draft-view draft-view-loading">
                <Loader2 className="size-5 animate-spin" />
                <span>Opening the document</span>
            </div>
        );
    }
    if (!draft) {
        return (
            <div className="draft-view draft-view-loading">
                <span>That document could not be opened.</span>
                <Button variant="ghost" onClick={onClose}>Close</Button>
            </div>
        );
    }

    // "In the knowledge base" is both fields, matching the server's check in
    // routes/drafts.py. Deleting the approved document nulls ingested_file_id
    // through ON DELETE SET NULL while status stays 'ingested', and the server
    // will happily approve it again; reading status alone would hide the button
    // that does so and strand the draft.
    const inKnowledgeBase = draft.status === 'ingested' && draft.ingested_file_id !== null;

    // One name per document rather than one per retrieved page, because a
    // twelve-page manual contributing eight passages is one source to a reader.
    const sourceNames = Array.from(
        new Set((draft.sources || []).map(s => s.file_name).filter(Boolean) as string[])
    );

    return (
        <div className="draft-view">
            <header className="draft-header">
                <input
                    className="draft-title-input"
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    aria-label="Document title"
                    placeholder="Untitled document"
                />
                <Button variant="ghost" size="icon-sm" onClick={onClose} title="Close">
                    <X className="size-4" />
                </Button>
            </header>

            {/* Stated every time, and it stays after editing. A document that
                looks like the business wrote it is the dangerous one. */}
            <div className="draft-provenance">
                <Sparkles className="size-3.5" />
                <span>
                    <strong>Written by SyntextAI</strong> from this workspace's documents.
                    Check it before anyone relies on it.
                </span>
            </div>

            {inKnowledgeBase ? (
                <div className="draft-ingested-note">
                    <Library className="size-3.5" />
                    In the knowledge base. This document can answer questions.
                </div>
            ) : (
                <div className="draft-not-ingested-note">
                    Not in the knowledge base yet, so it does not answer any questions.
                </div>
            )}

            <div className="draft-toolbar">
                <div className="draft-mode-toggle" role="group" aria-label="Edit or preview">
                    <button
                        type="button"
                        className={mode === 'preview' ? 'active' : ''}
                        onClick={() => setMode('preview')}
                    >
                        Read
                    </button>
                    <button
                        type="button"
                        className={mode === 'edit' ? 'active' : ''}
                        onClick={() => setMode('edit')}
                    >
                        Edit
                    </button>
                </div>

                <div className="draft-actions">
                    <Button variant="ghost" size="sm" onClick={handleSave} disabled={!dirty || saving}>
                        {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
                        {dirty ? 'Save' : 'Saved'}
                    </Button>
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleExport('docx')}
                        disabled={exporting !== null}
                        title="Download as a Word document"
                    >
                        {exporting === 'docx'
                            ? <Loader2 className="size-3.5 animate-spin" />
                            : <Download className="size-3.5" />}
                        Word
                    </Button>
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleExport('pdf')}
                        disabled={exporting !== null}
                        title="Download as a PDF"
                    >
                        {exporting === 'pdf'
                            ? <Loader2 className="size-3.5 animate-spin" />
                            : <Download className="size-3.5" />}
                        PDF
                    </Button>
                    {!inKnowledgeBase && (
                        <Button size="sm" onClick={() => setConfirmIngest(true)} disabled={ingesting}>
                            {ingesting ? <Loader2 className="size-3.5 animate-spin" /> : <Library className="size-3.5" />}
                            Add to knowledge base
                        </Button>
                    )}
                    <Button variant="ghost" size="icon-sm" onClick={() => setConfirmDelete(true)} title="Delete document">
                        <Trash2 className="size-3.5" />
                    </Button>
                </div>
            </div>

            <div className="draft-body">
                {mode === 'edit' ? (
                    <textarea
                        className="draft-editor"
                        value={content}
                        onChange={e => setContent(e.target.value)}
                        spellCheck
                        aria-label="Document text"
                    />
                ) : (
                    <article className="draft-rendered">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                    </article>
                )}
            </div>

            {sourceNames.length > 0 && (
                <footer className="draft-sources">
                    <div className="draft-sources-label">
                        Written from {sourceNames.length} {sourceNames.length === 1 ? 'document' : 'documents'}
                    </div>
                    <ul>
                        {sourceNames.map(name => (
                            <li key={name}><FileText className="size-3" /> {name}</li>
                        ))}
                    </ul>
                </footer>
            )}

            <ConfirmDialog
                open={confirmIngest}
                title="Add this to the knowledge base?"
                description={
                    "Once added, this document answers your team's questions and gets cited " +
                    "like any other. Read it first: anything wrong in it becomes an answer."
                }
                confirmLabel="Add it"
                cancelLabel="Not yet"
                onConfirm={handleIngest}
                onCancel={() => setConfirmIngest(false)}
            />
            <ConfirmDialog
                open={confirmDelete}
                title="Delete this document?"
                description={
                    inKnowledgeBase
                        ? "The copy already in your knowledge base stays. This removes the draft only."
                        : "This cannot be undone."
                }
                confirmLabel="Delete"
                cancelLabel="Keep it"
                destructive
                onConfirm={handleDelete}
                onCancel={() => setConfirmDelete(false)}
            />
        </div>
    );
};

export default DraftView;
