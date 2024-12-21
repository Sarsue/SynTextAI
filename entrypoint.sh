#!/bin/bash
DB_PATH="/app/db/docsynth.db"
LITESTREAM_CONFIG="/etc/litestream.yml"

echo "Checking if database exists at $DB_PATH..."
if [ ! -f "$DB_PATH" ]; then
    echo "Database not found. Restoring from GCS..."
    litestream restore -config "$LITESTREAM_CONFIG" -o "$DB_PATH"
    if [ $? -eq 0 ]; then
        echo "Database restored successfully."
    else
        echo "Failed to restore the database. Exiting."
        exit 1
    fi
else
    echo "Database already exists. Skipping restoration."
fi

# Start Litestream replication in the background
litestream replicate -config "$LITESTREAM_CONFIG" &

# Start the application (supervisor or any other process manager)
exec "$@"
