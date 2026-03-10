#!/usr/bin/env bash
set -e

# Write Google credentials from env vars to disk (Railway has no filesystem persistence)
if [ -n "$GOOGLE_CREDENTIALS_JSON" ]; then
    echo "$GOOGLE_CREDENTIALS_JSON" > "${GOOGLE_CREDENTIALS_FILE:-credentials.json}"
fi

if [ -n "$GOOGLE_TOKEN_JSON" ]; then
    echo "$GOOGLE_TOKEN_JSON" > "${GOOGLE_TOKEN_FILE:-token.json}"
fi

exec python -m app.main
