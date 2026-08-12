import React, { useEffect, useRef, useState } from 'react';
import { HardDrive } from 'lucide-react';

import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';

interface DriveImportProps {
    workspaceId: number | null;
    onImported: () => void;
    /**
     * Icon-only, for the composer, where it sits beside the paperclip because
     * both answer the same question: how do I get a document in. Separating
     * them by panel made one of the two answers hard to find.
     */
    compact?: boolean;
}

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;
const API_KEY = import.meta.env.VITE_GOOGLE_API_KEY as string | undefined;

/**
 * Only the files the customer picks, and only for as long as the import takes.
 *
 * `drive.file` grants access to the documents chosen in the picker and nothing
 * else. The obvious alternative, `drive.readonly`, is a *restricted* scope:
 * production use of it requires a paid third-party security assessment, and it
 * would mean asking a dental practice for read access to their entire Drive to
 * import four policies. This scope is cheaper to ship and easier to defend.
 */
const SCOPE = 'https://www.googleapis.com/auth/drive.file';

/**
 * The Cloud project number, which the picker needs in order to grant this app
 * access to the documents somebody picks.
 *
 * Without it the picker still opens and still returns file ids, and every
 * subsequent Drive call answers 404 for a file the customer is looking at.
 * That is `drive.file` working as designed: it grants access per file, to a
 * named app, and with no app id there is no app to grant it to.
 *
 * Derived from the client id rather than added as another environment
 * variable, because it is the same number and two copies of one fact drift.
 */
const APP_ID = (CLIENT_ID || '').split('-')[0];

const MIME_TYPES = [
    'application/pdf',
    'text/plain',
    'text/markdown',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    // Google Docs have no bytes of their own; the backend exports them to PDF,
    // which keeps the pagination a citation needs.
    'application/vnd.google-apps.document',
].join(',');

declare global {
    interface Window {
        google?: any;
        gapi?: any;
    }
}

/** Load a script once, and let everybody waiting share the same load. */
const loaded: Record<string, Promise<void>> = {};
const loadScript = (src: string): Promise<void> => {
    if (!loaded[src]) {
        loaded[src] = new Promise((resolve, reject) => {
            const el = document.createElement('script');
            el.src = src;
            el.async = true;
            el.onload = () => resolve();
            el.onerror = () => reject(new Error(`Could not load ${src}`));
            document.body.appendChild(el);
        });
    }
    return loaded[src];
};

const DriveImport: React.FC<DriveImportProps> = ({ workspaceId, onImported, compact = false }) => {
    const { user } = useUserContext();
    const { addToast } = useToast();
    const [busy, setBusy] = useState(false);
    const [ready, setReady] = useState(false);

    /**
     * The access token, kept for as long as it is valid.
     *
     * Google's browser flow issues short-lived tokens and has no refresh token
     * by design, so one is needed per session. Asking for a new one on every
     * click meant an authorisation popup every time somebody imported, which
     * reads as the app having forgotten them. Held in a ref rather than state:
     * nothing renders from it, and it must not survive a reload.
     *
     * Sixty seconds of headroom, so a token that expires mid-import fails
     * before the picker rather than halfway through fetching documents.
     */
    const tokenRef = useRef<{ value: string; expiresAt: number } | null>(null);

    /**
     * Google's libraries are loaded when this mounts, not when the button is
     * pressed, and that ordering is the whole reason the button works.
     *
     * Asking for a token opens a popup, and a browser only allows a popup that
     * is opened during a user gesture. Loading the scripts inside the click
     * handler means awaiting two network requests first, by which time the
     * gesture is over and Chrome blocks the window silently: no error, no
     * popup, a button that goes to "Opening Drive…" and stays there. Found
     * exactly that way.
     *
     * Loading here costs two script requests for anybody who can add
     * documents, which is the cheaper mistake.
     */
    useEffect(() => {
        if (!CLIENT_ID || !API_KEY) return;
        let cancelled = false;
        (async () => {
            try {
                await Promise.all([
                    loadScript('https://accounts.google.com/gsi/client'),
                    loadScript('https://apis.google.com/js/api.js'),
                ]);
                await new Promise<void>((resolve) => window.gapi.load('picker', resolve));
                if (!cancelled) setReady(true);
            } catch {
                // Leaving `ready` false disables the button rather than
                // offering one that cannot work.
            }
        })();
        return () => { cancelled = true; };
    }, []);

    // Hidden rather than broken when the app has no Google credentials
    // configured. A button that always fails teaches people the feature does
    // not work.
    if (!CLIENT_ID || !API_KEY) return null;

    const importPicked = async (accessToken: string, itemIds: string[]) => {
        if (!user || !workspaceId) return;
        const token = await user.getIdToken();
        const response = await fetch(
            `/api/v1/files/import?workspace_id=${workspaceId}`,
            {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${token}`,
                },
                body: JSON.stringify({
                    provider: 'google_drive',
                    access_token: accessToken,
                    item_ids: itemIds,
                }),
            },
        );

        if (!response.ok) {
            const detail = await response.json().catch(() => null);
            addToast(detail?.detail || 'Could not import from Drive.', 'error');
            return;
        }

        const body = await response.json();
        const imported = body.imported?.length ?? 0;
        // Partial success is reported as partial success. Ten documents where
        // one was deleted in Drive since picking should say so rather than
        // claim ten or claim failure.
        if (imported > 0) {
            addToast(
                `${imported} document${imported === 1 ? '' : 's'} importing. They will be ready shortly.`,
                'success',
            );
        }
        (body.skipped || []).forEach((s: { name: string; reason: string }) => {
            addToast(`${s.name}: ${s.reason}`, 'warning');
        });
        onImported();
    };

    /** Build and show the picker for a token we already hold. */
    const showPicker = (accessToken: string) => {
        const view = new window.google.picker.DocsView(window.google.picker.ViewId.DOCS)
            .setMimeTypes(MIME_TYPES)
            .setIncludeFolders(true)
            .setSelectFolderEnabled(false);

        const picker = new window.google.picker.PickerBuilder()
            .addView(view)
            .setOAuthToken(accessToken)
            .setDeveloperKey(API_KEY)
            // What turns "they chose this file" into "this app may read this
            // file". Without it every fetch answers 404 for a document the
            // customer is looking at. See APP_ID above.
            .setAppId(APP_ID)
            .enableFeature(window.google.picker.Feature.MULTISELECT_ENABLED)
            .setCallback(async (data: any) => {
                if (data.action === window.google.picker.Action.CANCEL) {
                    setBusy(false);
                    return;
                }
                if (data.action !== window.google.picker.Action.PICKED) return;

                const ids = (data.docs || []).map((d: any) => d.id);
                // The backend caps this too; stopping here spares somebody
                // picking a hundred files and waiting for a refusal.
                if (ids.length > 25) {
                    addToast('Pick 25 documents or fewer at a time.', 'warning');
                    setBusy(false);
                    return;
                }
                try {
                    await importPicked(accessToken, ids);
                } finally {
                    setBusy(false);
                }
            })
            .build();

        picker.setVisible(true);
    };

    // Deliberately not async, and nothing is awaited before the token request.
    // Everything Google needs is already loaded by the effect above, so the
    // popup opens inside the click that asked for it.
    const openPicker = () => {
        if (!workspaceId) {
            addToast('Choose a workspace to import into first.', 'info');
            return;
        }
        setBusy(true);
        try {
            // A token already granted and still valid, so no popup at all.
            const cached = tokenRef.current;
            if (cached && cached.expiresAt > Date.now()) {
                showPicker(cached.value);
                return;
            }

            const tokenClient = window.google.accounts.oauth2.initTokenClient({
                client_id: CLIENT_ID,
                scope: SCOPE,
                // Skips the account chooser for somebody already signed in
                // here, which is everybody: they cannot reach this button
                // otherwise.
                hint: user?.email || undefined,
                callback: (response: any) => {
                    if (response.error || !response.access_token) {
                        setBusy(false);
                        addToast('Google sign-in was cancelled.', 'info');
                        return;
                    }
                    tokenRef.current = {
                        value: response.access_token,
                        expiresAt:
                            Date.now() + (Number(response.expires_in || 3600) - 60) * 1000,
                    };
                    showPicker(response.access_token);
                },
            });

            // '' means "do not prompt if this app was already granted the
            // scope". consent every time would be correct only if we were
            // asking for something new each time, and we are not.
            tokenClient.requestAccessToken({ prompt: '' });
        } catch (e) {
            setBusy(false);
            addToast('Could not open Google Drive.', 'error');
        }
    };

    return (
        <button
            type="button"
            className={compact ? 'composer-drive-button' : 'kb-import-button'}
            onClick={openPicker}
            disabled={busy || !ready}
            title="Import from Google Drive"
            aria-label="Import from Google Drive"
        >
            <HardDrive className={compact ? 'size-4' : 'kb-import-icon'} aria-hidden="true" />
            {!compact && (busy ? 'Opening Drive…' : 'Import from Google Drive')}
        </button>
    );
};

export default DriveImport;
