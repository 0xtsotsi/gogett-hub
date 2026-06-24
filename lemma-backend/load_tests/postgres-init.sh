#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE supertokens;
    CREATE DATABASE lemma_datastore;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname lemma -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname lemma_datastore -c "CREATE EXTENSION IF NOT EXISTS vector;"
