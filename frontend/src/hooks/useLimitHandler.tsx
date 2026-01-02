import { useState, useCallback } from 'react';

export type LimitType = 'docs' | 'storage' | 'workspace' | 'general';

interface LimitError {
    error_code: string;
    detail?: string;
}

interface LimitInfo {
    showPrompt: boolean;
    limitType: LimitType;
    title: string;
    message: string;
}

export const useLimitHandler = () => {
    const [limitInfo, setLimitInfo] = useState<LimitInfo>({
        showPrompt: false,
        limitType: 'general',
        title: '',
        message: '',
    });

    const handleLimitError = useCallback((error: LimitError) => {
        const { error_code, detail } = error;

        switch (error_code) {
            case 'DOC_LIMIT_REACHED':
                setLimitInfo({
                    showPrompt: true,
                    limitType: 'docs',
                    title: 'Document Limit Reached',
                    message: detail || 'You\'ve reached the maximum of 5 documents on the free plan. Upgrade to premium for unlimited documents!',
                });
                break;

            case 'STORAGE_LIMIT_EXCEEDED':
                setLimitInfo({
                    showPrompt: true,
                    limitType: 'storage',
                    title: 'Storage Limit Exceeded',
                    message: detail || 'You\'ve exceeded your 500MB storage limit. Upgrade to premium for unlimited storage!',
                });
                break;

            case 'WORKSPACE_LIMIT_REACHED':
                setLimitInfo({
                    showPrompt: true,
                    limitType: 'workspace',
                    title: 'Workspace Limit Reached',
                    message: detail || 'Free plan is limited to 1 workspace. Upgrade to premium to create unlimited workspaces!',
                });
                break;

            default:
                setLimitInfo({
                    showPrompt: true,
                    limitType: 'general',
                    title: 'Limit Reached',
                    message: detail || 'You\'ve reached a limit on your free plan. Upgrade to premium for unlimited access!',
                });
                break;
        }
    }, []);

    const closePrompt = useCallback(() => {
        setLimitInfo(prev => ({ ...prev, showPrompt: false }));
    }, []);

    const checkResponseForLimitError = useCallback(async (response: Response) => {
        if (response.status === 402) {
            try {
                const data = await response.json();
                if (data.error_code) {
                    handleLimitError(data);
                    return true; // Limit error was handled
                }
            } catch (e) {
                console.error('Failed to parse limit error:', e);
            }
        }
        return false; // No limit error
    }, [handleLimitError]);

    return {
        limitInfo,
        handleLimitError,
        closePrompt,
        checkResponseForLimitError,
    };
};
