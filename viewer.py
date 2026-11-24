"""Debatviewer: toont trace.json en verdict.json live in je browser.

Start met `uv run viewer` (leest de root van deze repo) of `uv run viewer <map-of-trace.json>`.
Poort via VIEWER_PORT (standaard 8100).
"""
import http.server
import json
import os
import re
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
_SAFE_RUN_RE = re.compile(r"^[A-Za-z0-9_+.-]+$")


def _summarize_run(folder_id: str, trace_path: Path, verdict_path: Path) -> dict | None:
    if not trace_path.exists():
        return None
    try:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    verdict = None
    if verdict_path.exists():
        try:
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        except Exception:
            verdict = None

    beurten = trace.get("beurten") or []
    rollen = set(trace.get("raadsleden") or [])
    member_turns = sum(1 for b in beurten if b.get("soort") == "bericht" and b.get("spreker") in rollen)
    early_stop = any(b.get("soort") == "routering" and b.get("aan") is None for b in beurten) and member_turns < 10
    disputes = len(trace.get("geschillen") or [])
    voorspelling = (verdict or {}).get("voorspelling") or {}
    forecast_day = voorspelling.get("dag") if isinstance(voorspelling, dict) else None
    verbruik = trace.get("verbruik") or {}
    tokens_total = sum(
        int(v.get("invoer", 0) or 0) + int(v.get("uitvoer", 0) or 0)
        for v in verbruik.values()
        if isinstance(v, dict)
    )
    return {
        "id": folder_id,
        "run_id": trace.get("run_id") or folder_id,
        "status": trace.get("status") or "onbekend",
        "member_turns": member_turns,
        "early_stop": early_stop,
        "disputes": disputes,
        "forecast_day": forecast_day,
        "tokens_total": tokens_total,
        "has_verdict": verdict is not None,
    }


def _list_runs(base_dir: Path) -> list[dict]:
    items: list[dict] = []
    latest = _summarize_run("latest", base_dir / "trace.json", base_dir / "verdict.json")
    if latest:
        items.append(latest)

    runs_dir = base_dir / "runs"
    if runs_dir.is_dir():
        archived: list[dict] = []
        for entry in sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not entry.is_dir() or not _SAFE_RUN_RE.match(entry.name):
                continue
            info = _summarize_run(entry.name, entry / "trace.json", entry / "verdict.json")
            if info:
                archived.append(info)
        items.extend(archived)
    return items


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    doel = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    map_ = doel.parent if doel.is_file() else doel
    port = int(os.getenv("VIEWER_PORT", "8100"))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            pad = self.path.split("?")[0]
            if pad in ("/", "/index.html"):
                return self.stuur(ROOT / "viewer.html", "text/html; charset=utf-8")
            if pad in ("/trace.json", "/verdict.json"):
                return self.stuur(map_ / pad[1:], "application/json; charset=utf-8")
            if pad == "/api/runs":
                payload = json.dumps({"runs": _list_runs(map_)}, ensure_ascii=False).encode("utf-8")
                return self.stuur_bytes(payload, "application/json; charset=utf-8")
            if pad.startswith("/api/runs/"):
                parts = pad[len("/api/runs/"):].split("/")
                if len(parts) == 2 and _SAFE_RUN_RE.match(parts[0]) and parts[1] in ("trace.json", "verdict.json"):
                    return self.stuur(map_ / "runs" / parts[0] / parts[1], "application/json; charset=utf-8")
            self.send_error(404)

        def stuur_bytes(self, data: bytes, soort: str):
            self.send_response(200)
            self.send_header("Content-Type", soort)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def stuur(self, bestand, soort):
            if not bestand.exists():
                return self.send_error(404)
            return self.stuur_bytes(bestand.read_bytes(), soort)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Debatviewer op {url} (leest {map_}). Stop met Ctrl+C.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
