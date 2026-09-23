import React, { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { AnswerTrace as Trace } from './types';
import './AnswerTrace.css';

/**
 * How an answer was made, collapsed to one line under it.
 *
 * Trust comes from being able to look, not from being told. Every answer
 * already goes through documents chosen for it and a check of each citation
 * against its page; until this existed a reader saw none of that, so a
 * checked answer looked exactly like an unchecked one. Collapsed by default,
 * because most people want the answer and only some want the working.
 *
 * Built from the server's summary (api/agents/trace.py), which leaves out
 * models, costs and ids on purpose.
 */

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

const summaryLine = (t: Trace): string => {
    const parts: string[] = [];
    if (t.checked) {
        parts.push(`Checked ${plural(t.checked.claims, 'claim', 'claims')}`);
    }
    if (t.path === 'multi') {
        parts.push(`${parts.length ? 'across' : 'Asked'} ${plural(t.documents.length, 'document', 'documents')}`);
    } else if (!t.checked) {
        parts.push('Searched your documents');
    }
    let line = parts.join(' ');
    if (t.seconds != null) line += ` · ${t.seconds.toFixed(1)}s`;
    return line;
};

const AnswerTrace: React.FC<{ trace: Trace }> = ({ trace }) => {
    const [open, setOpen] = useState(false);
    const { documents, checked } = trace;
    const multi = trace.path === 'multi';

    return (
        <div className={`answer-trace ${open ? 'is-open' : ''}`}>
            <button
                type="button"
                className="answer-trace-toggle"
                aria-expanded={open}
                onClick={() => setOpen(o => !o)}
            >
                <ChevronRight className="answer-trace-chevron" aria-hidden="true" />
                <span>{summaryLine(trace)}</span>
            </button>

            {open && (
                <ol className="answer-trace-steps">
                    <li>
                        <div className="answer-trace-step">
                            {multi
                                ? `Asked ${plural(documents.length, 'document', 'documents')} separately`
                                : 'Searched your documents'}
                        </div>
                        <p className="answer-trace-note">
                            {multi
                                ? 'The question needed more than one document, so each was read on its own.'
                                : documents.length
                                    ? `Read passages from ${plural(documents.length, 'document', 'documents')}.`
                                    : 'Read the passages that best matched the question.'}
                        </p>
                        {documents.length > 0 && (
                            <ul className="answer-trace-docs">
                                {documents.map((d, i) => (
                                    <li key={i} className={d.answered ? '' : 'is-quiet'}>
                                        {d.name}
                                        {multi && !d.answered && <span> · had no answer</span>}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </li>

                    {multi && (
                        <li>
                            <div className="answer-trace-step">Combined their answers</div>
                            <p className="answer-trace-note">
                                Each fact keeps the page it came from.
                            </p>
                        </li>
                    )}

                    {checked && (
                        <li>
                            <div className="answer-trace-step">Checked each citation against its page</div>
                            <ul className="answer-trace-counts">
                                <li><span className="num">{checked.confirmed}</span> confirmed</li>
                                {checked.moved > 0 && (
                                    <li><span className="num">{checked.moved}</span> moved to the page that says it</li>
                                )}
                                {checked.unconfirmed > 0 && (
                                    <li className="is-caution">
                                        <span className="num">{checked.unconfirmed}</span> could not be confirmed, marked in the answer
                                    </li>
                                )}
                            </ul>
                        </li>
                    )}
                </ol>
            )}
        </div>
    );
};

export default AnswerTrace;
