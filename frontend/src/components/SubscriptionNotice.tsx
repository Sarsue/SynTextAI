import React from 'react';
import { useUserContext } from '../UserContext';
import './UsageQuota.css';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface SubscriptionNoticeProps {
    darkMode?: boolean;
}

/**
 * Says when a company's plan needs attention, and otherwise says nothing.
 *
 * This was UsageQuota, a panel showing documents and storage consumed against a
 * free allowance of five documents and 500 MB. That allowance no longer exists:
 * an organization has a subscription or it has nothing, so there is no ceiling
 * to draw a progress bar against and nobody the bar could be drawn for. The
 * only branch of it that described something real is this one — a subscription
 * that has lapsed, where the person needs to know why uploads stopped and where
 * to go.
 *
 * Deliberately not shown for a healthy plan. Seats and billing live in
 * settings, which is where somebody goes to look; repeating them beside the
 * knowledge base is noise on every screen for the sake of a state that is
 * almost always fine.
 */
const SubscriptionNotice: React.FC<SubscriptionNoticeProps> = ({ darkMode = false }) => {
    const { subscriptionStatus, isEntitled } = useUserContext();

    // Entitlement is the organization's, resolved by the backend. Reading this
    // user's own subscription marked every invited member as unpaid, since
    // staff hold no subscription of their own, and put a payment warning in
    // front of people inside a company that pays.
    const normalizedStatus = (subscriptionStatus || 'none').toLowerCase();
    const isPremium = isEntitled || normalizedStatus === 'active' || normalizedStatus === 'trialing';

    // 'none' is not a problem to report here. An organization that has not paid
    // cannot reach this screen at all, so the only way to see it is a status
    // that has not loaded yet, and a warning shown while the answer is still in
    // flight is a warning about nothing.
    const needsAttention = !isPremium && normalizedStatus !== 'none';

    if (!needsAttention) return null;

    return (
        <div className={`usage-quota ${darkMode ? 'dark-mode' : ''}`}>
            <div className="quota-header">
                <div className="quota-summary">
                    <span className="quota-icon"><AlertTriangle className="size-5" /></span>
                    <div className="quota-info">
                        <span className="quota-title">Access restricted</span>
                        <span className="quota-subtitle">
                            This organization's subscription is "{normalizedStatus}". Uploads stay
                            off until payment is fixed.
                        </span>
                    </div>
                </div>
                {/* The hash, not a bare path. This app is a HashRouter, so
                    href="/settings" loaded the site at an empty hash and landed
                    on the marketing homepage: somebody whose card had just
                    failed clicked "Fix payment" and was shown the sales page.

                    Named to the section as well, now that there is one, so it
                    opens on the form rather than on whatever section comes
                    first. */}
                <Button asChild size="sm">
                    <a href="#/settings/plan">Fix payment</a>
                </Button>
            </div>
        </div>
    );
};

export default SubscriptionNotice;
