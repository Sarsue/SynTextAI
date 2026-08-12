import React, { useState } from 'react';
import { HardDrive } from 'lucide-react';

import { useUserContext } from '../UserContext';
import { useToast } from '../contexts/ToastContext';

interface DriveImportProps {
    workspaceId: number | null;
    onImported: () => void;
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

const DriveImport: React.FC<DriveImportProps> = ({ workspaceId, onImported }) => {
    const { user } = useUserContext();
    const { addToast } = useToast();
    const [busy, setBusy] = useState(false);

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

    const openPicker = async () => {
        if (!workspaceId) {
            addToast('Choose a workspace to import into first.', 'info');
            return;
        }
        setBusy(true);
        try {
            await Promise.all([
                loadScript('https://accounts.google.com/gsi/client'),
                loadScript('https://apis.google.com/js/api.js'),
            ]);
            await new Promise<void>((resolve) => window.gapi.load('picker', resolve));

            const tokenClient = window.google.accounts.oauth2.initTokenClient({
                client_id: CLIENT_ID,
                scope: SCOPE,
                callback: (response: any) => {
                    if (response.error || !response.access_token) {
                        setBusy(false);
                        addToast('Google sign-in was cancelled.', 'info');
                        return;
                    }
                    const accessToken = response.access_token;

                    const view = new window.google.picker.DocsView(
                        window.google.picker.ViewId.DOCS,
                    )
                        .setMimeTypes(MIME_TYPES)
                        .setIncludeFolders(true)
                        .setSelectFolderEnabled(false);

                    const picker = new window.google.picker.PickerBuilder()
                        .addView(view)
                        .setOAuthToken(accessToken)
                        .setDeveloperKey(API_KEY)
                        .enableFeature(window.google.picker.Feature.MULTISELECT_ENABLED)
                        .setCallback(async (data: any) => {
                            if (data.action === window.google.picker.Action.CANCEL) {
                                setBusy(false);
                                return;
                            }
                            if (data.action !== window.google.picker.Action.PICKED) return;

                            const ids = (data.docs || []).map((d: any) => d.id);
                            // The backend caps this too; stopping here spares
                            // somebody picking a hundred files and waiting for
                            // a refusal.
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
                },
            });

            tokenClient.requestAccessToken({ prompt: '' });
        } catch (e) {
            setBusy(false);
            addToast('Could not open Google Drive.', 'error');
        }
    };

    return (
        <button type="button" className="kb-import-button" onClick={openPicker} disabled={busy}>
            <HardDrive className="kb-import-icon" aria-hidden="true" />
            {busy ? 'Opening Drive…' : 'Import from Google Drive'}
        </button>
    );
};

export default DriveImport;
