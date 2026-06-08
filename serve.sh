#!/usr/bin/env bash
# Serve the app with a production WSGI server (waitress) -- no debugger exposed.
# A password is required whenever you run this, since it's the tunnel/shared path.
#
#   ./serve.sh                 # prompts to set a password if none is set
#   APP_PASSWORD=hunter2 ./serve.sh
#
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "No .venv found. Run:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Make sure waitress is installed.
.venv/bin/python -c "import waitress" 2>/dev/null || .venv/bin/pip install -q waitress

if [ -z "${APP_PASSWORD:-}" ]; then
  echo "No APP_PASSWORD set. Since this is the shared/tunnel path, set one:"
  read -r -s -p "  Choose a password: " APP_PASSWORD; echo
  export APP_PASSWORD
fi
export APP_USER="${APP_USER:-admin}"

echo "Serving on http://127.0.0.1:5000   (user: $APP_USER)"
echo "Open a tunnel in another terminal to share it (see HOSTING.md)."
exec .venv/bin/python -m waitress --host=127.0.0.1 --port=5000 app:app
