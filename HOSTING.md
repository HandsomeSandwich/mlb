# Running & sharing the app

This app holds your Yahoo credentials and private league data, so the model is:
**run it on your own machine, and open a temporary tunnel only when you want to
reach it from your phone or show someone.** Nothing sensitive sits on a public
server, and your SQLite database never moves.

## 1. Local only (just you, on this Mac)

```bash
cd fantasy-baseball
.venv/bin/python app.py        # http://127.0.0.1:5000
```

No password, no fuss — it's only reachable from your own computer.

## 2. Share it / reach it remotely (tunnel)

Two terminals.

**Terminal A — serve it with a password** (production server, debugger off):

```bash
./serve.sh
# it'll prompt you to choose a password (user is "admin")
```

**Terminal B — open a tunnel** to `localhost:5000`. Pick one:

**Cloudflare (free, no account needed for a quick tunnel):**
```bash
brew install cloudflared          # one time
cloudflared tunnel --url http://localhost:5000
```
It prints a random `https://something.trycloudflare.com` URL. Anyone with that URL
**and your password** can view the app. Close the terminal to kill the link.

**ngrok (alternative):**
```bash
brew install ngrok                # one time
ngrok config add-authtoken <token>   # free account, one time
ngrok http 5000
```
Gives an `https://….ngrok-free.app` URL, same idea.

> The tunnel URL is HTTPS, so your password is encrypted in transit. When you're
> done, stop the tunnel (Ctrl-C) and the link dies. Quick-tunnel URLs are random and
> change every time — don't post them publicly.

## 3. Keeping the data fresh

The dashboards read a local SQLite DB you build with the ingest scripts. To refresh:

```bash
.venv/bin/python -m fantasybb.ingest season  --season 2026   # season totals
.venv/bin/python -m fantasybb.ingest games   --season 2026   # new game logs (resumable)
.venv/bin/python -m fantasybb.streaming refresh              # streaming board
.venv/bin/python -m fantasybb.league refresh                # standings/matchup/transactions
.venv/bin/python -m fantasybb.league slips                  # check for a new account slip
```

Want it automatic? Add a daily `cron` entry (macOS):
```bash
# crontab -e  — runs the league refresh every morning at 8am
0 8 * * * cd /Users/bettychitty/PycharmProjects/fantasy-baseball && .venv/bin/python -m fantasybb.league refresh >> data/cron.log 2>&1
```

## Security checklist

- **Always run `./serve.sh` (with a password) before opening a tunnel** — never tunnel
  the plain `app.py` (it has no auth and, in debug, an exploitable console).
- `data/yahoo_oauth.json` stays on your machine and is gitignored. Don't upload it
  anywhere.
- Kill the tunnel when you're not using it.
- If you ever do want an always-on URL, see Option B/C we discussed (PythonAnywhere or
  a small VM) — but put it behind this same password (or stronger) first.
