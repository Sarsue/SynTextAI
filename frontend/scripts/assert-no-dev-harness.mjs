/**
 * Refuse to produce a build that carries the local sign-in harness.
 *
 * WHY THIS EXISTS RATHER THAN A RULE ABOUT BRANCHES
 *
 * The harness has to be usable locally and must never reach a real domain.
 * That was arranged by keeping the file off master, and it broke in the one
 * way that arrangement always breaks: 269acb7 deleted it on master, the next
 * merge from master carried the deletion onto develop, and develop lost the
 * ability to sign in for testing. `devSignIn.ts` stayed on disk the whole time
 * with nothing importing it, so the harness looked present and did nothing,
 * which is worse than either outcome on its own.
 *
 * A file cannot be on one branch and not another without somebody remembering
 * which direction merges run. So it lives on both, and this decides whether it
 * ships. `import.meta.env.DEV` already eliminates it from a production build;
 * this checks that the elimination actually happened, which is the part nobody
 * would notice failing.
 *
 * The ways it could start shipping are all quiet: `vite build --mode
 * development`, a refactor that lifts the import out of its guard, a bundler
 * upgrade that stops tree-shaking a dynamic import behind a false constant.
 * None of those announce themselves and all of them are one command away.
 *
 * WHAT IS SEARCHED FOR, AND WHY THAT AND NOT THE OBVIOUS THING
 *
 * `__syntextDevSignIn`, the global the harness defines, because it is unique to
 * this codebase. NOT `signInWithCustomToken`: that is a firebase/auth export
 * and it is in every build already, because the app bundles that package for
 * Google sign-in. Asserting on it would fail every build forever and teach
 * whoever hits it to delete the check.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const BUILD_DIR = new URL('../build', import.meta.url).pathname;
const MARKER = '__syntextDevSignIn';

function* files(dir) {
    for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) yield* files(full);
        else yield full;
    }
}

let checked = 0;
const carrying = [];
for (const file of files(BUILD_DIR)) {
    checked += 1;
    if (readFileSync(file, 'utf8').includes(MARKER)) carrying.push(file);
}

// A build that produced nothing would otherwise pass this silently, which is
// the same shape of bug the check exists to prevent.
if (checked === 0) {
    console.error(`No files found under ${BUILD_DIR}. Did the build run?`);
    process.exit(1);
}

if (carrying.length) {
    console.error(
        `\nThis build carries the local sign-in harness:\n` +
        carrying.map(f => `  ${f}`).join('\n') +
        `\n\n${MARKER} must not exist outside a developer's machine. Check that the` +
        ` import in src/index.tsx is still inside \`if (import.meta.env.DEV)\`, and` +
        ` that this was not built with --mode development.\n`
    );
    process.exit(1);
}

console.log(`No sign-in harness in the build (${checked} files checked).`);
