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

/**
 * Nothing is defined anywhere but a developer's own machine.
 *
 * Two guards now stand between this and a real domain, and they fail in
 * different ways on purpose.
 *
 * There were three. The first was that this file lived only on develop, so a
 * release could not carry it, and that one is gone: develop was merged to
 * master on 2026-08-06 and the file came with it. Rather than leave a comment
 * claiming a guarantee that no longer holds, it is written down here.
 *
 * What remains. The import sits behind `import.meta.env.DEV`, so a production
 * build eliminates it: verified on the build shipped that day, where
 * `grep __syntextDevSignIn frontend/build/assets/*.js` found nothing. That is
 * a guarantee about build configuration, and configuration drifts: a
 * `vite build --mode development`, a refactor that lifts the import out of its
 * guard, a CI change made a year from now. Any of those ships it quietly and
 * nothing would say so.
 *
 * This last check does not depend on anything staying correct. It is evaluated
 * where the page is actually running.
 *
 * It is worth the two lines because of __syntextDevToken specifically. The
 * sign-in and sign-out hooks are close to inert even if shipped, since a custom
 * token cannot be minted without the service-account private key, and whoever
 * holds that key already owns the project. But handing out the signed-in user's
 * ID token is a different thing: any script executing on this origin, including
 * one arriving through a compromised dependency, would get a working bearer
 * credential on its first line rather than having to understand the app. The
 * Content-Security-Policy is still Report-Only, so it is not stopping injected
 * script either.
 */
const isLocal = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);

if (isLocal) {
    // So a test can call the API directly and check that the backend refuses,
    // not merely that the button is hidden.
    window.__syntextDevToken = async () =>
        (await auth.currentUser?.getIdToken()) ?? null;

    window.__syntextDevSignIn = async (token: string) => {
        const credential = await signInWithCustomToken(auth, token);
        return credential.user.uid;
    };

    window.__syntextDevSignOut = () => signOut(auth);
} else {
    // Say why, rather than leaving a test that calls this failing with
    // "undefined is not a function" against a deployed origin.
    console.warn('[syntext] dev sign-in helpers are not available outside localhost');
}

export {};
