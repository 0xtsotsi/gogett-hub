#!/usr/bin/env bash
# =============================================================================
# setup_todo.sh — Create a Lemma todo app pod with a table and seed records
#
# Usage:
#   LEMMA_TOKEN=<token> bash setup_todo.sh
#   LEMMA_TOKEN=<token> LEMMA_BASE_URL=https://api.lemma.work bash setup_todo.sh
#
# Or authenticate via the CLI first:
#   lemma config set --base-url https://api.lemma.work --token <token>
#   bash setup_todo.sh
#
# For local dev with self-signed certs:
#   LEMMA_SSL_NO_VERIFY=1 LEMMA_TOKEN=<token> bash setup_todo.sh
# =============================================================================
set -euo pipefail

BASE_URL="${LEMMA_BASE_URL:-https://api.lemma.work}"
TOKEN="${LEMMA_TOKEN:-}"
SSL_FLAG=""
if [[ "${LEMMA_SSL_NO_VERIFY:-0}" == "1" ]]; then
  SSL_FLAG="--no-verify-ssl"
fi

if [[ -z "$TOKEN" ]]; then
  echo "❌  LEMMA_TOKEN is required. Export it before running this script."
  echo "    LEMMA_TOKEN=<your-token> bash setup_todo.sh"
  exit 1
fi

LEMMA="lemma --base-url $BASE_URL --token $TOKEN $SSL_FLAG"

echo "=== Lemma Todo App Setup ==="
echo "Base URL: $BASE_URL"
echo ""

# ---------------------------------------------------------------------------
# 1. Find or confirm the first org
# ---------------------------------------------------------------------------
echo "→ Fetching organizations..."
ORGS=$($LEMMA org list)
ORG_ID=$(echo "$ORGS" | python3 -c "
import json, sys
orgs = json.load(sys.stdin)
items = orgs.get('items', [])
print(items[0]['id'] if items else 'NO_ORG')
")

if [[ "$ORG_ID" == "NO_ORG" ]]; then
  echo "❌  No organizations found. Create one first."
  exit 1
fi

echo "   Using org: $ORG_ID"

# ---------------------------------------------------------------------------
# 2. Create (or reuse) the todolist pod
# ---------------------------------------------------------------------------
echo "→ Looking for existing 'todolist' pod..."
PODS=$($LEMMA pod list --org "$ORG_ID")
POD_ID=$(echo "$PODS" | python3 -c "
import json, sys
pods = json.load(sys.stdin)
for p in pods.get('items', []):
    if p.get('name') == 'todolist':
        print(p['id'])
        break
else:
    print('NOT_FOUND')
")

if [[ "$POD_ID" == "NOT_FOUND" ]]; then
  echo "→ Creating 'todolist' pod in org $ORG_ID..."
  # pod create returns the bare pod object (no {data} envelope).
  CREATE_RESULT=$($LEMMA pod create todolist --org "$ORG_ID" --description "Todo list demo pod")
  POD_ID=$(echo "$CREATE_RESULT" | python3 -c "
import json, sys
pod = json.load(sys.stdin)
print(pod.get('id', ''))
")
  echo "   Created pod: $POD_ID"
else
  echo "   Reusing existing pod: $POD_ID"
fi

# ---------------------------------------------------------------------------
# 3. Ensure the 'todos' table exists with the right columns
# ---------------------------------------------------------------------------
echo "→ Checking for 'todos' table..."
TBL_LIST=$($LEMMA table list --pod "$POD_ID")
TBL_EXISTS=$(echo "$TBL_LIST" | python3 -c "
import json, sys
d = json.load(sys.stdin)
found = any((i.get('table_name') or i.get('name')) == 'todos' for i in d.get('items', []))
print('yes' if found else 'no')
")

if [[ "$TBL_EXISTS" == "no" ]]; then
  echo "→ Creating 'todos' table..."
  # A personal todo list is per-user data, so the default enable_rls=true (RLS on)
  # fits: each member sees and edits only their own todos. Omitting enable_rls
  # keeps the default. Set "enable_rls": false instead for a shared/team list.
  $LEMMA table create todos --pod "$POD_ID" -d '{
    "name": "todos",
    "columns": [
      {"name": "title",       "type": "TEXT",     "required": true},
      {"name": "description", "type": "TEXT",     "required": false},
      {"name": "status",      "type": "TEXT",     "required": true},
      {"name": "priority",    "type": "TEXT",     "required": false},
      {"name": "due_date",    "type": "DATETIME", "required": false}
    ]
  }' > /dev/null
  echo "   Created."
else
  echo "   Already exists."
fi

# ---------------------------------------------------------------------------
# 4. Seed a few sample records
# ---------------------------------------------------------------------------
echo "→ Seeding sample todo records..."
EXISTING_TODOS=$($LEMMA record list todos --pod "$POD_ID")
for TITLE_STATUS_PRIORITY in \
  "Set up Lemma SDK:todo:high" \
  "Build todo app UI:in_progress:medium" \
  "Write unit tests:todo:low"
do
  TITLE=$(echo "$TITLE_STATUS_PRIORITY" | cut -d: -f1)
  STATUS=$(echo "$TITLE_STATUS_PRIORITY" | cut -d: -f2)
  PRIORITY=$(echo "$TITLE_STATUS_PRIORITY" | cut -d: -f3)

  EXISTS=$(echo "$EXISTING_TODOS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
title = sys.argv[1]
print('yes' if any((item.get('title') == title) for item in d.get('items', [])) else 'no')
" "$TITLE")

  if [[ "$EXISTS" == "yes" ]]; then
    echo "   = $TITLE already present"
    continue
  fi

  # record create takes a JSON payload of column -> value (-d / -f).
  $LEMMA record create todos --pod "$POD_ID" \
    -d "$(python3 -c "
import json, sys
print(json.dumps({'title': sys.argv[1], 'status': sys.argv[2], 'priority': sys.argv[3]}))
" "$TITLE" "$STATUS" "$PRIORITY")" > /dev/null
  echo "   + $TITLE [$STATUS/$PRIORITY]"
done

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "✅  Setup complete!"
echo ""
echo "   Pod ID:    $POD_ID"
echo "   Table:     todos"
echo ""
echo "Run the todo app:"
echo "   cd lemma-typescript/examples/todo-app"
echo "   VITE_LEMMA_POD_ID=$POD_ID npm run dev"
echo ""
