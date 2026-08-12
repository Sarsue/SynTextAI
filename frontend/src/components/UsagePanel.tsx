import React, { useEffect, useState } from 'react';

import { useUserContext } from '../UserContext';
import './UsagePanel.css';

interface Usage {
    window_days: number;
    questions_asked: number;
    most_active: { email: string; questions: number }[];
    documents: {
        total: number;
        by_status: Record<string, number>;
        failed: number;
        never_retrieved: string[];
    };
    feedback: {
        helpful: number;
        unhelpful: number;
        reasons: { reason: string; count: number }[];
    };
}

/** The four chips from the answer feedback form, in words rather than keys. */
const REASON_LABELS: Record<string, string> = {
    wrong: 'The answer was wrong',
    incomplete: 'The answer was incomplete',
    not_in_documents: 'Not in our documents',
    wrong_source: 'Cited the wrong source',
};

/**
 * What this company is getting out of the product.
 *
 * Owner and admin only, enforced by the API rather than by hiding this: the
 * numbers are about other people, and a leaderboard of colleagues' question
 * counts is a different product from the one being sold.
 *
 * Every figure comes from our own database. PostHog holds page views and
 * clicks, which is the wrong question for an owner and the wrong place to
 * answer it from, since filtering one company's activity out of a shared
 * analytics account is a tenancy problem we do not need to have.
 */
const UsagePanel: React.FC = () => {
    const { user, activeOrganizationId, orgContext } = useUserContext();
    const [usage, setUsage] = useState<Usage | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    // The same capability the route requires, not an approximation of it.
    // `can_manage_members` is INVITE_MEMBER, which is exactly what
    // /usage asserts, so this hides precisely what the API would refuse.
    // Defaults to hidden while the context loads: an owner seeing the panel
    // appear a moment late is cheaper than a member being shown a section that
    // then fails.
    const canSee = orgContext?.can_manage_members ?? false;

    useEffect(() => {
        if (!user || !activeOrganizationId || !canSee) {
            setLoading(false);
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const token = await user.getIdToken();
                const res = await fetch(
                    `/api/v1/organizations/${activeOrganizationId}/usage`,
                    { headers: { Authorization: `Bearer ${token}` } },
                );
                if (!res.ok) throw new Error(`status ${res.status}`);
                const body = await res.json();
                if (!cancelled) setUsage(body);
            } catch {
                if (!cancelled) setError('Could not load usage right now.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [user, activeOrganizationId, canSee]);

    if (!canSee) return null;
    if (loading) return <p className="usage-status">Loading usage…</p>;
    if (error) return <p className="usage-status">{error}</p>;
    if (!usage) return null;

    const { documents, feedback } = usage;
    const rated = feedback.helpful + feedback.unhelpful;

    return (
        <div className="usage-panel">
            <p className="usage-window">Last {usage.window_days} days</p>

            <div className="usage-figures">
                <div className="usage-figure">
                    <span className="usage-number">{usage.questions_asked}</span>
                    <span className="usage-label">questions asked</span>
                </div>
                <div className="usage-figure">
                    <span className="usage-number">{documents.total}</span>
                    <span className="usage-label">documents</span>
                </div>
                {/* Only when there are any. A zero here is good news and does
                    not need a box of its own competing for attention. */}
                {documents.failed > 0 && (
                    <div className="usage-figure usage-figure-warn">
                        <span className="usage-number">{documents.failed}</span>
                        <span className="usage-label">failed to process</span>
                    </div>
                )}
            </div>

            {usage.most_active.length > 0 && (
                <div className="usage-block">
                    <h3 className="usage-heading">Who is using it</h3>
                    <ul className="usage-list">
                        {usage.most_active.map((person) => (
                            <li key={person.email}>
                                <span>{person.email}</span>
                                <span className="usage-count">{person.questions}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {rated > 0 && (
                <div className="usage-block">
                    <h3 className="usage-heading">What your team thought of the answers</h3>
                    <p className="usage-line">
                        {feedback.helpful} helpful, {feedback.unhelpful} not
                    </p>
                    {feedback.reasons.length > 0 && (
                        <ul className="usage-list">
                            {feedback.reasons.map((r) => (
                                <li key={r.reason}>
                                    <span>{REASON_LABELS[r.reason] ?? r.reason}</span>
                                    <span className="usage-count">{r.count}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {documents.never_retrieved.length > 0 && (
                <div className="usage-block">
                    <h3 className="usage-heading">Documents nothing has needed yet</h3>
                    <p className="usage-line usage-muted">
                        No answer has drawn on these. Usually that means nobody has asked
                        about them, occasionally that they did not read properly.
                    </p>
                    <ul className="usage-list">
                        {documents.never_retrieved.map((name) => (
                            <li key={name}><span>{name}</span></li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
};

export default UsagePanel;
