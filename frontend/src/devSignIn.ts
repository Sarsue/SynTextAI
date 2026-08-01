/**
 * Dev-only: sign in from an automated test, without a Google popup.
 *
 * Loaded from index.tsx behind `if (import.meta.env.DEV)`, and only as a
 * dynamic import, so the production bundle never contains this file. Check with
 * `grep __syntextDevSignIn frontend/build/assets/*.js` after a build; it must
 * find nothing.
 *
 * This is not a back door. It calls Firebase's ordinary signInWithCustomToken,
 * and a custom token can only be minted with the project's service-account
 * private key. Anyone holding that key already controls the Firebase project
 * outright, so this grants nothing that the key does not already grant. There
 * is no password path, no bypass of the backend's token verification, and no
 * new dependency: signInWithCustomToken ships inside firebase/auth, which the
 * app already bundles for Google sign-in.
 *
 * Never point this at production. It exists so the app can be driven end to end
 * against the local stack.
 */
import { signInWithCustomToken, signOut } from 'firebase/auth';
import { auth } from './firebase';

declare global {
    interface Window {
        __syntextDevSignIn?: (token: string) => Promise<string>;
        __syntextDevSignOut?: () => Promise<void>;
        __syntextDevToken?: () => Promise<string | null>;
    }
}

// So a test can call the API directly and check that the backend refuses, not
// merely that the button is hidden.
window.__syntextDevToken = async () =>
    (await auth.currentUser?.getIdToken()) ?? null;

window.__syntextDevSignIn = async (token: string) => {
    const credential = await signInWithCustomToken(auth, token);
    return credential.user.uid;
};

window.__syntextDevSignOut = () => signOut(auth);

export {};
