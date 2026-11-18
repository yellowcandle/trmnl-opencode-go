#!/usr/bin/env python3
"""Fetch OpenCode Go quota and push merge variables to a TRMNL webhook plugin.

Primary source: `opencode api GET /api/experimental/usage/limits` (uses OpenCode auth).
Fallback: read local OpenCode sqlite credential DB and call the Console Go usage endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONSOLE = "https://opencode.ai/console"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # ms epoch
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def fmt_reset(value: Any) -> tuple[str | None, int | None, str | None]:
    dt = parse_dt(value)
    if not dt:
        return None, None, None
    ms = int(dt.timestamp() * 1000)
    iso = dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    delta = dt - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        human = "now"
    else:
        days, rem = divmod(secs, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        parts.append(f"{mins}m")
        human = " ".join(parts)
    return iso, ms, human


def normalize_meter(key: str, label: str, meter: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meter, dict):
        return {
            "key": key,
            "label": label,
            "percent": None,
            "remaining_percent": None,
            "limited": None,
            "resets_at": None,
            "resets_at_ms": None,
            "resets_in": None,
            "status": "unknown",
        }
    percent = meter.get("percent")
    try:
        percent_f = float(percent) if percent is not None else None
    except Exception:
        percent_f = None
    if percent_f is not None:
        percent_f = max(0.0, min(100.0, percent_f))
    limited = bool(meter.get("limited")) if meter.get("limited") is not None else None
    resets_at, resets_at_ms, resets_in = fmt_reset(meter.get("resetsAt", meter.get("resets_at")))
    remaining = None if percent_f is None else round(max(0.0, 100.0 - percent_f), 2)
    status = "limited" if limited else ("ok" if percent_f is not None else "unknown")
    return {
        "key": key,
        "label": label,
        "percent": percent_f,
        "remaining_percent": remaining,
        "limited": limited,
        "resets_at": resets_at,
        "resets_at_ms": resets_at_ms,
        "resets_in": resets_in,
        "status": status,
    }


def build_payload(data: dict[str, Any] | None, source: str | None = None, error: str | None = None) -> dict[str, Any]:
    workspace = {}
    go = {}
    budgets = []
    if isinstance(data, dict):
        ws = data.get("workspace") or {}
        if isinstance(ws, dict):
            workspace = {
                "id": ws.get("id"),
                "name": ws.get("name") or "OpenCode Console",
                "usage_url": ws.get("usageURL") or ws.get("usage_url"),
            }
        go_raw = data.get("go")
        if isinstance(go_raw, dict):
            windows = [
                normalize_meter("rolling", "5 hour", go_raw.get("rolling")),
                normalize_meter("weekly", "Weekly", go_raw.get("weekly")),
                normalize_meter("monthly", "Monthly", go_raw.get("monthly")),
            ]
            go = {
                "available": True,
                "windows": windows,
                "rolling": windows[0],
                "weekly": windows[1],
                "monthly": windows[2],
                "any_limited": any(w.get("limited") for w in windows),
                "max_percent": max((w.get("percent") or 0) for w in windows),
            }
        else:
            go = {"available": False, "windows": [], "rolling": None, "weekly": None, "monthly": None}
        b = data.get("budgets")
        if isinstance(b, list):
            for item in b[:6]:
                if not isinstance(item, dict):
                    continue
                budgets.append(
                    {
                        "id": item.get("id"),
                        "limit": item.get("limit"),
                        "spent": item.get("spent"),
                        "scope": item.get("scope"),
                        "per": item.get("per"),
                        "coverage": item.get("coverage"),
                        "shared": item.get("shared"),
                        "resets_at": fmt_reset(item.get("resetsAt", item.get("resets_at")))[0],
                    }
                )
    else:
        go = {"available": False, "windows": [], "rolling": None, "weekly": None, "monthly": None}

    if not workspace:
        workspace = {"id": None, "name": "OpenCode Console", "usage_url": "https://opencode.ai/console"}

    status = "ok" if go.get("available") and not error else ("unavailable" if not go.get("available") else "error")
    if error and go.get("available"):
        status = "partial"

    return {
        "workspace": workspace,
        "go": go,
        "budgets": budgets,
        "updated_at": now_iso(),
        "source": source,
        "status": status,
        "error": error,
    }


def fetch_via_opencode_cli() -> dict[str, Any] | None:
    cmd = ["opencode", "api", "GET", "/api/experimental/usage/limits"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    try:
        payload = json.loads(out)
    except Exception:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    return build_payload(data, source="opencode-api")


def candidate_db_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("OPENCODE_DB")
    if env:
        paths.append(Path(env).expanduser())
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        paths.append(Path(xdg).expanduser() / "opencode" / "opencode.db")
    home = Path.home()
    paths.extend(
        [
            home / ".local" / "share" / "opencode" / "opencode.db",
            home / "Library" / "Application Support" / "opencode" / "opencode.db",
        ]
    )
    # de-dupe preserving order
    seen = set()
    out = []
    for p in paths:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        out.append(p)
    return out


def fetch_console_credential() -> dict[str, str] | None:
    # Explicit overrides (useful for CI or manual runs)
    token = os.environ.get("OPENCODE_CONSOLE_TOKEN")
    org = os.environ.get("OPENCODE_WORKSPACE_ID") or os.environ.get("OPENCODE_ORG_ID")
    server = os.environ.get("OPENCODE_CONSOLE_SERVER", DEFAULT_CONSOLE)
    if token and org:
        return {"token": token, "org_id": org, "server": server}

    for db_path in candidate_db_paths():
        if not db_path.exists():
            continue
        try:
            con = sqlite3.connect(str(db_path))
            con.row_factory = sqlite3.Row
            # Prefer active Console OAuth credential with orgID metadata.
            rows = con.execute(
                """
                select value, active, time_created from credential
                where integration_id = 'opencode'
                order by active desc, time_created desc, id desc
                """
            ).fetchall()
            for row in rows:
                raw = row["value"]
                if not raw:
                    continue
                try:
                    value = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    continue
                if not isinstance(value, dict) or value.get("type") != "oauth":
                    continue
                access = value.get("access")
                meta = value.get("metadata") or {}
                org_id = meta.get("orgID") or meta.get("org_id")
                srv = meta.get("server") or server
                if access and org_id:
                    con.close()
                    return {"token": str(access), "org_id": str(org_id), "server": str(srv), "org_name": str(meta.get("orgName") or "")}
            con.close()
        except Exception:
            continue
    return None


UA = os.environ.get("SYNC_USER_AGENT", "trmnl-opencode-go/1.0")


def http_get_json(url: str, headers: dict[str, str], timeout: int = 20) -> tuple[int, Any]:
    h = {"User-Agent": UA, **headers}
    req = urllib.request.Request(url, headers=h, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        try:
            return e.code, json.loads(body.decode("utf-8", "replace")) if body else None
        except Exception:
            return e.code, body.decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError(str(e)) from e
    try:
        return status, json.loads(body.decode("utf-8", "replace")) if body else None
    except Exception:
        return status, body.decode("utf-8", "replace")


def fetch_via_console_api() -> dict[str, Any] | None:
    cred = fetch_console_credential()
    if not cred:
        return build_payload(None, source="console-api", error="OpenCode Console credential not found (sign in with `opencode` or set OPENCODE_CONSOLE_TOKEN+OPENCODE_WORKSPACE_ID)")

    server = cred.get("server") or DEFAULT_CONSOLE
    # normalize retired host like OpenCode does
    server = server.replace("https://console.opencode.ai", DEFAULT_CONSOLE)
    origin = urllib.parse.urlsplit(server).scheme + "://" + urllib.parse.urlsplit(server).netloc
    if not urllib.parse.urlsplit(server).scheme:
        origin = "https://opencode.ai"
    usage_url = origin + "/zen/go/v1/usage"
    headers = {
        "Authorization": f"Bearer {cred['token']}",
        "x-opencode-org-id": cred["org_id"],
        "Accept": "application/json",
    }
    try:
        status, body = http_get_json(usage_url, headers)
    except Exception as e:
        return build_payload(None, source="console-api", error=f"Go usage request failed: {e}")

    if status == 403:
        data = {"workspace": {"id": cred.get("org_id"), "name": cred.get("org_name") or "OpenCode Console", "usageURL": f"{DEFAULT_CONSOLE}/{cred.get('org_id')}/activity/overview"}, "go": None, "budgets": []}
        return build_payload(data, source="console-api", error="Go allowance unavailable for this workspace (not subscribed or access denied)")
    if status in (401, 400):
        msg = "Console token rejected/expired; run OpenCode to refresh sign-in"
        if isinstance(body, dict):
            m = body.get("error") or body.get("message")
            if isinstance(m, dict):
                m = m.get("message") or m.get("code")
            if isinstance(m, str) and m:
                msg = m
        return build_payload(None, source="console-api", error=msg)
    if status < 200 or status >= 300:
        return build_payload(None, source="console-api", error=f"Go usage HTTP {status}")

    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return build_payload(None, source="console-api", error="Unexpected Go usage response")

    def meter(m: Any) -> dict[str, Any] | None:
        if not isinstance(m, dict):
            return None
        resets = m.get("resetsAt")
        dt = parse_dt(resets)
        return {
            "percent": m.get("percent"),
            "limited": str(m.get("status", "")).lower() == "rate-limited",
            "resetsAt": int(dt.timestamp() * 1000) if dt else resets,
        }

    data = {
        "workspace": {
            "id": cred.get("org_id"),
            "name": cred.get("org_name") or "OpenCode Console",
            "usageURL": f"{DEFAULT_CONSOLE}/{cred.get('org_id')}/activity/overview",
        },
        "go": {
            "rolling": meter(usage.get("rolling")),
            "weekly": meter(usage.get("weekly")),
            "monthly": meter(usage.get("monthly")),
        },
        "budgets": [],
    }
    return build_payload(data, source="console-api")


def post_webhook(url: str, merge_variables: dict[str, Any], timeout: int = 20) -> tuple[int, Any]:
    body = json.dumps({"merge_variables": merge_variables}, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        try:
            return e.code, json.loads(raw.decode("utf-8", "replace")) if raw else None
        except Exception:
            return e.code, raw.decode("utf-8", "replace")
    try:
        return status, json.loads(raw.decode("utf-8", "replace")) if raw else None
    except Exception:
        return status, raw.decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync OpenCode Go quota to a TRMNL webhook plugin")
    ap.add_argument("--webhook-url", default=os.environ.get("TRMNL_WEBHOOK_URL"), help="TRMNL plugin webhook URL")
    ap.add_argument("--dry-run", action="store_true", help="Print merge variables JSON and do not POST")
    ap.add_argument("--print-only", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-opencode-api", action="store_true", help="Skip `opencode api` and use direct Console API")
    args = ap.parse_args(argv)

    payload = None
    if not args.no_opencode_api:
        payload = fetch_via_opencode_cli()
    if payload is None:
        payload = fetch_via_console_api()
    if payload is None:
        payload = build_payload(None, source=None, error="Unable to determine OpenCode Go quota")

    if args.dry_run or args.print_only or not args.webhook_url:
        print(json.dumps(payload, indent=2, sort_keys=False))
        if not args.webhook_url and not args.dry_run and not args.print_only:
            print("No webhook URL provided (set TRMNL_WEBHOOK_URL or --webhook-url). Dry-run output printed.", file=sys.stderr)
            return 0 if payload.get("go", {}).get("available") else 2
        return 0

    status, resp = post_webhook(args.webhook_url, payload)
    if status >= 200 and status < 300:
        print(f"Posted quota to TRMNL webhook ({status})")
        return 0
    print(f"Webhook POST failed: HTTP {status}: {resp}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
