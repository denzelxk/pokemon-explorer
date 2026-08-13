#!/bin/sh
set -e

DB_PATH="${POKEAPI_DB:-data/pokeapi.db}"

if [ ! -f "$DB_PATH" ]; then
    echo "No database found at $DB_PATH - running ETL (this can take a while for a full pull)..."
    if [ -n "$POKEAPI_LIMIT" ]; then
        python etl_pokeapi.py --db "$DB_PATH" --limit "$POKEAPI_LIMIT" --workers "${POKEAPI_WORKERS:-8}"
    else
        python etl_pokeapi.py --db "$DB_PATH" --workers "${POKEAPI_WORKERS:-8}"
    fi
fi

exec streamlit run dashboard.py --server.address=0.0.0.0 -- --db="$DB_PATH"
