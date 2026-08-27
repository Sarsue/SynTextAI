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
                if (res.status === 410 && user) {
                    // Already used, by this person, moments ago.
                    //
                    // Signing in accepts every invite waiting for the address,
                    // because the link is how somebody learns they were invited
                    // and not a step the join depends on. So the ordinary path
                    // through this page — click accept, get sent to sign in,
                    // come back — returns to a token that sign-in has already
                    // consumed. Reading that as a broken link told somebody who
                    // had just successfully joined that their invite was
                    // unavailable, and left them on a dead end.
                    setPageStatus('done');
                    navigate('/select-organization', { replace: true });
                    return;
                }
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
    }, [token, user, navigate]);

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
            const accept = () => fetch(`/api/v1/workspaces/invites/${token}/accept`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${idToken}` },
            });

            let res = await accept();
            let registeredOnRetry = false;

            // "User not found" means Firebase knows them and we do not, which
            // happens when the registration call at sign-in did not land. That
            // call is not retried anywhere and its failure is not fatal by
            // design, so without this the person is left holding a valid invite
            // that can never be accepted, on a card whose only button is Go
            // home. Register and try once more: the second attempt is the whole
            // recovery, and one retry is enough because a permanent failure
            // gives the same answer twice.
            if (res.status === 404) {
                await fetch('/api/v1/users?intent=signin', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        Authorization: `Bearer ${idToken}`,
                    },
                    body: JSON.stringify({ firebase_uid: user.uid, email: user.email }),
                }).catch(() => undefined);
                registeredOnRetry = true;
                res = await accept();
            }

            if (res.ok) {
                // Enter the organization we just joined, rather than bouncing
                // through the chooser to rediscover it.
                const data = await res.json().catch(() => ({}));
                if (data.organization_id) {
                    await setActiveOrganization(data.organization_id);
                }
                setPageStatus('done');
                setTimeout(() => navigate('/chat'), 1800);
            } else if (registeredOnRetry) {
                // Registering accepts every invite waiting for the address, so
                // by the time the retry ran the invite was already used, by us,
                // a moment earlier. The second accept then reports it as invalid
                // or already used and that reads as failure, but the person is
                // a member: verified in the database after this exact sequence,
                // invite 'accepted' and the row present in organization_members.
                // Showing them "Invite unavailable" at that point is the app
                // calling its own success a failure.
                setPageStatus('done');
                navigate('/select-organization', { replace: true });
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
        // An invite that reads "already accepted" to somebody not signed in
        // means they joined and are simply not signed in on this device. "Go
        // home" is the one thing that cannot help them, so offer the sign-in
        // that can. Anything genuinely dead still gets Go home.
        const alreadyAccepted = /accepted/i.test(errorMsg);
        return (
            <div className="auth-page">
                <div className="invite-card">
                    <h2 className="invite-title">
                        {alreadyAccepted ? 'You have already joined' : 'Invite unavailable'}
                    </h2>
                    <p className="invite-sub">
                        {alreadyAccepted
                            ? 'This invite has been used. Sign in to reach your team.'
                            : errorMsg}
                    </p>
                    {alreadyAccepted ? (
                        <Button variant="outline" className="w-full" onClick={() => navigate('/login')}>Sign in</Button>
                    ) : (
                        <Button variant="outline" className="w-full" onClick={() => navigate('/')}>Go home</Button>
                    )}
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
