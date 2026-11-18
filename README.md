# TRMNL × OpenCode Go quota

TRMNL private plugin that displays your [OpenCode Go](https://opencode.ai/docs/console/go/) usage allowance (5-hour, weekly, monthly).

## How it works

OpenCode Go quota is served by the OpenCode Console and is not a public unauthenticated feed. This repo provides:

- **`plugin/`** – Liquid templates + form/settings metadata for a TRMNL [Private Plugin](https://help.trmnl.com/en/articles/9510536-custom-plugins).
- **`scripts/sync.py`** – local sync that reads your OpenCode Console sign-in (from the local OpenCode database / `opencode`), fetches Go allowance, and pushes it to a TRMNL **Webhook** plugin.

Recommended strategy: **Webhook** (your machine pushes; tokens stay local).

## Data source (OpenCode)

`sync.py` resolves quota in this order:

1. `opencode api GET /api/experimental/usage/limits` (uses OpenCode’s own auth/refresh when the local server is available).
2. Direct read of the local OpenCode data DB (`~/.local/share/opencode/opencode.db`, macOS/Linux XDG) to find the active Console OAuth credential, then `GET https://opencode.ai/zen/go/v1/usage` with `Authorization: Bearer <access>` and `x-opencode-org-id: <workspace>`.

> Direct DB mode is read-only and does **not** refresh/rotate OAuth tokens. If the token is expired, run `opencode` and sign in again, or prefer mode 1.

Endpoint details are based on OpenCode source (`packages/core/src/usage-limits.ts`, protocol `usage.limits`).

## Setup

### 1. Create the TRMNL Private Plugin

1. In TRMNL, create a **Private Plugin**.
2. Set **Strategy** to **Webhook** (note the plugin Webhook URL / UUID).
3. Paste markup:
   - **Shared**: `plugin/shared.liquid`
   - **Full**: `plugin/full.liquid`
   - **Half horizontal**: `plugin/half_horizontal.liquid`
   - **Half vertical**: `plugin/half_vertical.liquid`
   - **Quadrant**: `plugin/quadrant.liquid`
4. Save. (Developer edition may be required for webhook/custom plugin features depending on account.)

### 2. Push quota from your machine

```bash
export TRMNL_WEBHOOK_URL="https://trmnl.com/api/custom_plugins/<uuid>"
python3 scripts/sync.py
```

Options:

- `--dry-run` – print merge variables JSON, do not POST.
- `--once` – default; fetch and post once.
- `TRMNL_WEBHOOK_URL` – webhook endpoint.
- `OPENCODE_DB` – override OpenCode sqlite DB path.
- `OPENCODE_CONSOLE_TOKEN` / `OPENCODE_WORKSPACE_ID` – override direct API credentials.

Schedule with cron/launchd/systemd timer, e.g. every 30 minutes:

```cron
*/30 * * * * TRMNL_WEBHOOK_URL=... /usr/bin/python3 /path/to/scripts/sync.py >> /tmp/trmnl-opencode-go.log 2>&1
```

## Merge variables

`scripts/sync.py` sends `merge_variables` shaped like:

```json
{
  "workspace": {"id": "wrk_...", "name": "Default"},
  "go": {
    "available": true,
    "rolling": {"label": "5 hour", "percent": 12, "limited": false, "resets_at": "...", "resets_at_ms": 0, "remaining_percent": 88},
    "weekly":  {"label": "Weekly", "percent": 3,  "limited": false, ...},
    "monthly": {"label": "Monthly","percent": 40, "limited": false, ...}
  },
  "updated_at": "2026-11-18T00:00:00Z",
  "error": null
}
```

When quota is unavailable, `go.available` is false and `error` is set.

## Local preview

Open `plugin/preview.html` in a browser (loads TRMNL `plugins.css/js` from trmnl.com) to sanity-check markup with sample data.

## Notes

- Go allowance windows: **5-hour (rolling)**, **weekly**, **monthly**; limits/rates vary by plan and model. See [OpenCode Go docs](https://opencode.ai/docs/console/go/).
- This is an unofficial community plugin, not affiliated with TRMNL or OpenCode.

## Local development with `trmnlp`

This repo is structured for [trmnlp](https://github.com/usetrmnl/trmnlp):

- `src/` — Liquid templates (`shared.liquid` + layout views) and `settings.yml` (plugin id `544538`)
- `.trmnlp.yml` — local preview variables matching `scripts/sync.py` output
- `bin/trmnlp` — Docker wrapper (no local Ruby required)
- `.github/workflows/trmnl.yml` — lint on PR, push on `main` (needs `TRMNL_API_KEY` secret)

```bash
# Docker (recommended if Ruby < 4.0)
./bin/trmnlp lint
./bin/trmnlp serve            # http://localhost:4567
TRMNL_API_KEY=... ./bin/trmnlp push --force

# Or with Ruby 4+ gem
gem install trmnl_preview
trmnlp lint
trmnlp serve
```
