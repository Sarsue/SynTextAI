#!/bin/sh
# Run Vite beside the Docker stack rather than instead of it.
#
# The container already holds port 3000 and serves the API there, so the dev
# server takes another port and proxies /api and /ws across. vite.config.mts
# reads both of these; see the comment on `server` there.
#
# Exists as a script rather than inline environment in .claude/launch.json
# because the preview runner reads a literal :3000 in the launch arguments as
# the port being started, and refuses: the port is busy, correctly, with
# Colima's forwarder for the API this proxies to.
set -e
cd "$(dirname "$0")"
export VITE_DEV_PORT="${PORT:-5173}"
export VITE_API_TARGET="http://localhost:${API_PORT:-3000}"
exec npm run dev
