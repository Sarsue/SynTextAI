import React, { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AnswerProgress } from './types';
import './StreamingAnswer.css';

/**
 * An answer while it is being made: what step it is on, how long it has
 * taken, and the text written so far.
 *
 * NOTHING HERE IS CHECKED YET, AND NOTHING HERE IS CLICKABLE.
 *
 * Citations are checked against their pages only after the whole answer is
 * written, and the final message (real page links, anything unconfirmed
 * marked) replaces this bubble when that is done. Until then:
 *
 * - No link renders as a link. The server strips links a model invents, but
 *   only from the final answer; streamed text has not been through that, so a
 *   document written to plant a link could otherwise put a live one on screen
 *   for a few seconds.
 * - Citation markers become plain numbers, counted in order of appearance,
 *   which is how the final answer numbers them too.
 */

const STAGES: Record<string, (info: Record<string, number>) => string> = {
    searching: () => 'Searching your documents',
    reading: (i) => `Reading ${i.documents ?? ''} ${i.documents === 1 ? 'document' : 'documents'}`.replace('  ', ' '),
    writing: () => 'Writing',
    checking: (i) => `Checking ${i.claims} ${i.claims === 1 ? 'claim' : 'claims'}`,
};

const MARKER = /\[Segments?\s*(\d+(?:\s*,\s*\d+)*)[^\]]*\]/gi;

/** Markers as numbers in order of first appearance; a marker still being
 *  written at the end of the text is hidden until it is complete. */
const displayText = (raw: string): string => {
    const order = new Map<number, number>();
    const numbered = raw.replace(MARKER, (_m, nums: string) =>
        nums.split(',').map((n) => {
            const k = Number(n.trim());
            if (!order.has(k)) order.set(k, order.size + 1);
            return `[${order.get(k)}]`;
        }).join(''),
    );
    return numbered.replace(/\[[^\]\n]*$/, '');
};

const StreamingAnswer: React.FC<{ progress: AnswerProgress }> = ({ progress }) => {
    const [now, setNow] = useState(Date.now());
    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), 100);
        return () => clearInterval(id);
    }, []);

    const seconds = progress.elapsed + Math.max(0, now - progress.receivedAt) / 1000;
    const label = (STAGES[progress.stage] ?? (() => 'Working'))(progress.info);
    const text = useMemo(() => displayText(progress.text), [progress.text]);

    return (
        <div className="chat-message received streaming-answer" aria-live="polite" aria-busy="true">
            {text && (
                <div className="message-content">
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                            a: ({ children }) => <span>{children}</span>,
                            img: () => null,
                        }}
                    >
                        {text}
                    </ReactMarkdown>
                </div>
            )}
            <div className="streaming-status">
                <span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>
                <span>{label} · {seconds.toFixed(1)}s</span>
            </div>
            {text && progress.stage !== 'checking' && (
                <div className="streaming-note">Citations are checked before the answer is final.</div>
            )}
        </div>
    );
};

export default StreamingAnswer;
