import React, { useState, useEffect } from 'react';
import { useUserContext } from '../UserContext';
import './UsageQuota.css';

interface UsageQuotaProps {
    darkMode?: boolean;
}

interface QuotaData {
    files_used: number;
    files_limit: number;
    storage_used_bytes: number;
    storage_limit_bytes: number;
    workspaces_used: number;
    workspaces_limit: number;
}

const UsageQuota: React.FC<UsageQuotaProps> = ({ darkMode = false }) => {
    const { user, subscriptionStatus } = useUserContext();
    const [quotaData, setQuotaData] = useState<QuotaData | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [showDetails, setShowDetails] = useState(false);

    // Backend entitlement rules:
    // - premium: active | trialing
    // - free: none (or missing)
    // - restricted: anything else (past_due, unpaid, canceled, etc.)
    const normalizedStatus = (subscriptionStatus || 'none').toLowerCase();
    const isPremium = normalizedStatus === 'active' || normalizedStatus === 'trialing';
    const isFreeUser = normalizedStatus === 'none';
    const isRestricted = !isPremium && !isFreeUser;

    useEffect(() => {
        // Only show quota for free users. Restricted users should focus on fixing payment.
        if (user && isFreeUser) {
            fetchQuotaData();
        } else {
            setIsLoading(false);
        }
    }, [user, isFreeUser]);

    const fetchQuotaData = async () => {
        if (!user) return;

        try {
            const idToken = await user.getIdToken();

            const quotaResponse = await fetch('/api/v1/users/quota', {
                headers: { 'Authorization': `Bearer ${idToken}` },
            });

            if (quotaResponse.ok) {
                const data: QuotaData = await quotaResponse.json();
                setQuotaData(data);
            }
            
            setIsLoading(false);
        } catch (error) {
            console.error('Error fetching quota data:', error);
            setIsLoading(false);
        }
    };

    const formatBytes = (bytes: number): string => {
        if (bytes === 0) return '0 MB';
        const mb = bytes / (1024 * 1024);
        return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
    };

    const getUsagePercentage = (used: number, limit: number): number => {
        return Math.min((used / limit) * 100, 100);
    };

    const getUsageColor = (percentage: number): string => {
        if (percentage >= 90) return '#dc3545'; // Red
        if (percentage >= 70) return '#ffc107'; // Yellow
        return '#28a745'; // Green
    };

    // Don't show for premium users
    if (isPremium) {
        return null;
    }

    // For restricted users (past_due/unpaid/canceled/etc.), reflect entitlement state instead of showing quota.
    if (isRestricted) {
        return (
            <div className={`usage-quota ${darkMode ? 'dark-mode' : ''}`}>
                <div className="quota-header">
                    <div className="quota-summary">
                        <span className="quota-icon">⚠️</span>
                        <div className="quota-info">
                            <span className="quota-title">Access Restricted</span>
                            <span className="quota-subtitle">
                                Your subscription status is "{normalizedStatus}". Fix payment to re-enable uploads.
                            </span>
                        </div>
                    </div>
                    <a href="/settings" className="upgrade-button">
                        Fix Payment
                    </a>
                </div>
            </div>
        );
    }

    if (isLoading || !quotaData) {
        return null;
    }

    const filesPercentage = getUsagePercentage(quotaData.files_used, quotaData.files_limit);
    const storagePercentage = getUsagePercentage(quotaData.storage_used_bytes, quotaData.storage_limit_bytes);
    const isNearLimit = filesPercentage >= 70 || storagePercentage >= 70;
    const isAtLimit = filesPercentage >= 100 || storagePercentage >= 100;

    return (
        <div className={`usage-quota ${darkMode ? 'dark-mode' : ''}`}>
            <div className="quota-header" onClick={() => setShowDetails(!showDetails)}>
                <div className="quota-summary">
                    <span className="quota-icon">
                        {isAtLimit ? '⚠️' : isNearLimit ? '📊' : '💾'}
                    </span>
                    <div className="quota-info">
                        <span className="quota-title">Free Plan Usage</span>
                        <span className="quota-subtitle">
                            {quotaData.files_used}/{quotaData.files_limit} docs • {formatBytes(quotaData.storage_used_bytes)}/{formatBytes(quotaData.storage_limit_bytes)}
                        </span>
                    </div>
                </div>
                <button className="expand-btn" aria-label={showDetails ? 'Hide details' : 'Show details'}>
                    {showDetails ? '−' : '+'}
                </button>
            </div>

            {showDetails && (
                <div className="quota-details">
                    {/* Files Quota */}
                    <div className="quota-item">
                        <div className="quota-item-header">
                            <span className="quota-label">📄 Documents</span>
                            <span className="quota-value">
                                {quotaData.files_used} / {quotaData.files_limit}
                            </span>
                        </div>
                        <div className="progress-bar">
                            <div 
                                className="progress-fill"
                                style={{ 
                                    width: `${filesPercentage}%`,
                                    backgroundColor: getUsageColor(filesPercentage)
                                }}
                            />
                        </div>
                    </div>

                    {/* Storage Quota */}
                    <div className="quota-item">
                        <div className="quota-item-header">
                            <span className="quota-label">💾 Storage</span>
                            <span className="quota-value">
                                {formatBytes(quotaData.storage_used_bytes)} / {formatBytes(quotaData.storage_limit_bytes)}
                            </span>
                        </div>
                        <div className="progress-bar">
                            <div 
                                className="progress-fill"
                                style={{ 
                                    width: `${storagePercentage}%`,
                                    backgroundColor: getUsageColor(storagePercentage)
                                }}
                            />
                        </div>
                    </div>

                    {/* Workspaces Quota */}
                    <div className="quota-item">
                        <div className="quota-item-header">
                            <span className="quota-label">📁 Workspaces</span>
                            <span className="quota-value">
                                {quotaData.workspaces_used} / {quotaData.workspaces_limit}
                            </span>
                        </div>
                        <div className="progress-bar">
                            <div 
                                className="progress-fill"
                                style={{ 
                                    width: `${getUsagePercentage(quotaData.workspaces_used, quotaData.workspaces_limit)}%`,
                                    backgroundColor: getUsageColor(getUsagePercentage(quotaData.workspaces_used, quotaData.workspaces_limit))
                                }}
                            />
                        </div>
                    </div>

                    {/* Upgrade CTA */}
                    {(isNearLimit || isAtLimit) && (
                        <div className="upgrade-cta">
                            <p className="cta-text">
                                {isAtLimit 
                                    ? '⚠️ You\'ve reached your limit!' 
                                    : '📈 Running low on space?'}
                            </p>
                            <p className="cta-description">
                                Upgrade to premium for unlimited documents, storage, and workspaces!
                            </p>
                            <a href="/settings" className="upgrade-button">
                                Upgrade Now
                            </a>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default UsageQuota;
