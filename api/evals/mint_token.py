"""Mint a Firebase ID token for the benchmark runner, locally.

WHY THIS EXISTS

`run_benchmark.py` talks to the real API, so it needs a real Firebase ID token.
Getting one by hand meant signing into the app in a browser and copying it out
of devtools, which is slow, expires in an hour, and puts a live credential
through a clipboard.

WHAT IT DOES

Two steps, both ordinary Firebase:

  1. Mint a custom token with the project's service-account key. Only the
     holder of that key can do this, and anyone holding it already controls the
     project outright, so this grants nothing new.
  2. Exchange it for an ID token at Google's identity endpoint, which is
     exactly what the browser SDK does after `signInWithCustomToken`.

NOT FOR PRODUCTION

It reads the local service-account file and the local API key. Pointing it at a
production project would mint a real user's credentials on a developer laptop.
The same reasoning as frontend/src/devSignIn.ts, which this is the server-side
twin of.

USE

    TOKEN=$(python api/evals/mint_token.py --email you@example.com)

The token is printed alone on stdout so it can be captured, and everything else
goes to stderr. Treat it as a password: it is one for the next hour.
"""
import argparse
import os
import sys

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="an existing user in this Firebase project")
    ap.add_argument(
        "--credentials",
        default=os.getenv("FIREBASE_CREDENTIALS", "/app/api/config/credentials.json"),
    )
    ap.add_argument("--api-key", default=os.getenv("FIREBASE_API_KEY"))
    args = ap.parse_args()

    if not args.api_key:
        print("need --api-key or FIREBASE_API_KEY", file=sys.stderr)
        return 2

    import firebase_admin
    from firebase_admin import auth, credentials

    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(credentials.Certificate(args.credentials))

    user = auth.get_user_by_email(args.email)
    custom = auth.create_custom_token(user.uid)

    resp = requests.post(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken",
        params={"key": args.api_key},
        json={"token": custom.decode(), "returnSecureToken": True},
        timeout=30,
    )
    if resp.status_code != 200:
        # Deliberately not dumping the body: the failure responses from this
        # endpoint can echo request material back.
        print(f"exchange failed: HTTP {resp.status_code}", file=sys.stderr)
        return 1

    print(f"minted for {args.email} (uid {user.uid[:6]}...)", file=sys.stderr)
    print(resp.json()["idToken"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
