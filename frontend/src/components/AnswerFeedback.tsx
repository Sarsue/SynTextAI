import React, { useEffect, useRef, useState } from 'react';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { FeedbackReason, MessageFeedback } from './types';
import { useUserContext } from '../UserContext';

/**
 * Was this answer any good?
 *
 * WHY THIS EXISTS
 *
 * Every claim about answer quality is currently our own. The benchmark scores
 * 18-19 of 27, but those are 27 questions we wrote about documents we chose.
 * What a dental practice asks its own insurance PDFs is unknown, and so is
 * which of those answers were wrong. This is the only part of the product that
 * finds out.
 *
 * WHY A CHIP AND A COMMENT, NOT ONE OR THE OTHER
 *
 * The chip is countable, so "eleven of fourteen complaints were wrong_source"
 * is a sentence the report can produce. The comment is where the actual
 * diagnosis lives, because "cited the 2019 policy, we are on the 2024 one" is
 * something no fixed list would ever have contained. Neither replaces the
 * other.
 *
 * WHY THE FORM ONLY APPEARS ON THUMBS-DOWN
 *
 * Somebody happy with an answer is already reading the next thing. Asking them
 * to categorise their happiness costs the rating we just earned. A complaint is
 * the moment somebody is willing to spend ten seconds.
 */

const CHIPS: { value: FeedbackReason; label: string }[] = [
    { value: 'wrong', label: 'Wrong answer' },
    { value: 'incomplete', label: 'Incomplete' },
    { value: 'not_in_documents', label: 'Not in my documents' },
    { value: 'wrong_source', label: 'Cited the wrong place' },
];

// Matches MAX_FEEDBACK_COMMENT on the route, which rejects anything longer.
const MAX_COMMENT = 500;

interface AnswerFeedbackProps {
    messageId: number;
    feedback: MessageFeedback | null | undefined;
    onChange: (messageId: number, feedback: MessageFeedback | null) => void;
}

const AnswerFeedback: React.FC<AnswerFeedbackProps> = ({ messageId, feedback, onChange }) => {
    const { user, activeOrganizationId } = useUserContext();
    const [expanded, setExpanded] = useState(false);
    const [comment, setComment] = useState('');
    const [saving, setSaving] = useState(false);
    const formRef = useRef<HTMLDivElement>(null);

    const rating = feedback?.rating ?? null;

    // The answer being rated is usually the last one, so the form opens right
    // where the composer covers it: the chips showed and the comment box did
    // not, which reads as there being nothing more to say. Scroll it into view
    // instead of relying on the reader to find it.
    useEffect(() => {
        if (!expanded) return;
        const id = window.setTimeout(() => {
            formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }, 50);
        return () => window.clearTimeout(id);
    }, [expanded]);

    const send = async (next: MessageFeedback | null) => {
        if (!user) return;
        const previous = feedback ?? null;
        // Optimistic: a thumb that waits on a round trip feels broken, and
        // this is a throwaway gesture nobody will repeat if it stutters.
        onChange(messageId, next);
        setSaving(true);
        try {
            const token = await user.getIdToken();
            const query = activeOrganizationId ? `?organization_id=${activeOrganizationId}` : '';
            const url = `/api/v1/messages/${messageId}/feedback${query}`;
            const res = next === null
                ? await fetch(url, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
                : await fetch(url, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                    body: JSON.stringify(next),
                });
            if (!res.ok) throw new Error(String(res.status));
        } catch {
            // Put it back. Silently: a failed rating is not worth interrupting
            // somebody's work over, and the thumb returning to its old state
            // is itself the message.
            onChange(messageId, previous);
        } finally {
            setSaving(false);
        }
    };

    const press = (value: 1 | -1) => {
        if (rating === value) {
            // Same thumb again means "never mind".
            setExpanded(false);
            setComment('');
            void send(null);
            return;
        }
        if (value === 1) {
            setExpanded(false);
            setComment('');
            void send({ rating: 1 });
            return;
        }
        // Record the complaint immediately, then ask why. Somebody who clicks
        // away without picking a chip has still told us something.
        setExpanded(true);
        void send({ rating: -1 });
    };

    const chooseChip = (reason: FeedbackReason) => {
        void send({ rating: -1, reason, comment: comment.trim() || null });
    };

    const saveComment = () => {
        const trimmed = comment.trim();
        void send({ rating: -1, reason: feedback?.reason ?? null, comment: trimmed || null });
        setExpanded(false);
    };

    return (
        <div className="answer-feedback">
            <div className="answer-feedback-thumbs">
                <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Good answer"
                    aria-pressed={rating === 1}
                    className={rating === 1 ? 'is-active' : ''}
                    disabled={saving}
                    onClick={() => press(1)}
                    title="Good answer"
                >
                    <ThumbsUp className="size-3.5" />
                </Button>
                <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Bad answer"
                    aria-pressed={rating === -1}
                    className={rating === -1 ? 'is-active' : ''}
                    disabled={saving}
                    onClick={() => press(-1)}
                    title="Bad answer"
                >
                    <ThumbsDown className="size-3.5" />
                </Button>
            </div>

            {expanded && rating === -1 && (
                <div className="answer-feedback-form" ref={formRef}>
                    <div className="answer-feedback-chips">
                        {CHIPS.map((chip) => (
                            <button
                                key={chip.value}
                                type="button"
                                className={`answer-feedback-chip ${feedback?.reason === chip.value ? 'is-selected' : ''}`}
                                aria-pressed={feedback?.reason === chip.value}
                                onClick={() => chooseChip(chip.value)}
                            >
                                {chip.label}
                            </button>
                        ))}
                    </div>
                    <div className="answer-feedback-comment">
                        <input
                            type="text"
                            maxLength={MAX_COMMENT}
                            value={comment}
                            placeholder="What was wrong? (optional)"
                            aria-label="What was wrong with this answer"
                            onChange={(e) => setComment(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') saveComment(); }}
                        />
                        <Button variant="outline" size="sm" onClick={saveComment} disabled={saving}>
                            Send
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
};

export default AnswerFeedback;
