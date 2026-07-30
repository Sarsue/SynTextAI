import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useUserContext } from '../UserContext';
import '../index.css';
import { Button } from '@/components/ui/button';

type InviteStatus = 'loading' | 'ready' | 'invalid' | 'accepting' | 'done' | 'error';

interface InviteInfo {
    workspace_id: number;
    workspace_name: string;
    organization_name?: string | null;
    email: string;
    status: string;
}

const AcceptInvite: React.FC = () => {
    const { token } = useParams<{ token: string }>();
    const { user, darkMode, setActiveOrganization } = useUserContext();
    const navigate = useNavigate();

    const [pageStatus, setPageStatus] = useState<InviteStatus>('loading');
    const [inviteInfo, setInviteInfo] = useState<InviteInfo | null>(null);
    const [errorMsg, setErrorMsg] = useState('');

    // Validate the token on load
    useEffect(() => {
        if (!token) { setPageStatus('invalid'); return; }
        fetch(`/api/v1/workspaces/invites/${token}`)
            .then(async res => {
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    setErrorMsg(data.detail || 'This invite link is invalid or has expired.');
                    setPageStatus('invalid');
                } else {
                    setInviteInfo(await res.json());
                    setPageStatus('ready');
                }
            })
            .catch(() => { setErrorMsg('Could not reach the server.'); setPageStatus('invalid'); });
    }, [token]);

    const handleAccept = async () => {
        if (!user) {
            // Store token and redirect to login; login will redirect back
            sessionStorage.setItem('pending_invite_token', token || '');
            navigate('/login');
            return;
        }

        setPageStatus('accepting');
        try {
            const idToken = await user.getIdToken();
            const res = await fetch(`/api/v1/workspaces/invites/${token}/accept`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${idToken}` },
            });
            if (res.ok) {
                // Enter the organization we just joined, rather than bouncing
                // through the chooser to rediscover it.
                const data = await res.json().catch(() => ({}));
                if (data.organization_id) {
                    await setActiveOrganization(data.organization_id);
                }
                setPageStatus('done');
                setTimeout(() => navigate('/chat'), 1800);
            } else {
                const data = await res.json().catch(() => ({}));
                setErrorMsg(data.detail || 'Could not accept the invite.');
                setPageStatus('error');
            }
        } catch {
            setErrorMsg('Something went wrong. Please try again.');
            setPageStatus('error');
        }
    };

    if (pageStatus === 'loading') {
        return (
            <div className="auth-page">
                <div className="invite-card"><p className="auth-loading">Checking invite...</p></div>
            </div>
        );
    }

    if (pageStatus === 'invalid' || pageStatus === 'error') {
        return (
            <div className="auth-page">
                <div className="invite-card">
                    <h2 className="invite-title">Invite unavailable</h2>
                    <p className="invite-sub">{errorMsg}</p>
                    <Button variant="outline" className="w-full" onClick={() => navigate('/')}>Go home</Button>
                </div>
            </div>
        );
    }

    if (pageStatus === 'done') {
        return (
            <div className="auth-page">
                <div className="invite-card">
                    <h2 className="invite-title">You're in</h2>
                    <p className="invite-sub">You've joined <strong>{inviteInfo?.organization_name || inviteInfo?.workspace_name}</strong>. Redirecting...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="auth-page">
            <div className="invite-card">
                <h2 className="invite-title">You've been invited</h2>
                <p className="invite-sub">
                    Join <strong>{inviteInfo?.organization_name || inviteInfo?.workspace_name}</strong> on Syntext
                </p>
                {!user && (
                    <p className="invite-hint">You'll be asked to sign in first. No account needed, and nothing to pay: your team's plan covers you.</p>
                )}
                <Button
                    variant="outline"
                    className="w-full mt-2"
                    onClick={handleAccept}
                    disabled={pageStatus === 'accepting'}
                >
                    {pageStatus === 'accepting' ? 'Joining...' : 'Accept invite'}
                </Button>
            </div>
        </div>
    );
};

export default AcceptInvite;
