"""Debatviewer, podium en arcade: tonen trace.json en verdict.json live in je browser.

Start met `uv run viewer` (leest de root van deze repo) of `uv run viewer <map-of-trace.json>`.
De viewer van de kit staat op /, ons podium op /podium, de arcade van Abdullah op /arcade.
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
VEILIGE_NAAM = re.compile(r"[A-Za-z0-9_+.-]+")


def samenvatting(id_, map_):
    """Wat de arcade over een run wil weten in haar keuzelijst."""
    try:
        trace = json.loads((map_ / "trace.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        verdict = json.loads((map_ / "verdict.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        verdict = None
    beurten, rollen = trace.get("beurten") or [], set(trace.get("raadsleden") or [])
    aantal = sum(b.get("soort") == "bericht" and b.get("spreker") in rollen for b in beurten)
    return {
        "id": id_, "run_id": trace.get("run_id") or id_, "status": trace.get("status") or "onbekend", "member_turns": aantal,
        "early_stop": aantal < 10 and any(b.get("soort") == "routering" and b.get("aan") is None for b in beurten),
        "disputes": len(trace.get("geschillen") or []), "forecast_day": ((verdict or {}).get("voorspelling") or {}).get("dag"),
        "tokens_total": sum((v.get("invoer") or 0) + (v.get("uitvoer") or 0) for v in (trace.get("verbruik") or {}).values()),
        "has_verdict": verdict is not None,
    }


def alle_runs(map_):
    archief = sorted((m for m in (map_ / "runs").glob("*") if m.is_dir() and VEILIGE_NAAM.fullmatch(m.name)), key=lambda m: m.name, reverse=True)
    return [r for r in [samenvatting("latest", map_), *(samenvatting(m.name, m) for m in archief)] if r]


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
            if pad in ("/podium", "/arcade"):
                return self.stuur(ROOT / f"{pad[1:]}.html", "text/html; charset=utf-8")
            if pad == "/api/runs":
                return self.stuur_data(json.dumps({"runs": alle_runs(map_)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            delen = pad.split("/")
            if len(delen) == 5 and delen[1:3] == ["api", "runs"] and VEILIGE_NAAM.fullmatch(delen[3]) and delen[3].strip(".") and delen[4] in ("trace.json", "verdict.json"):
                return self.stuur(map_ / "runs" / delen[3] / delen[4], "application/json; charset=utf-8")
            if pad in ("/trace.json", "/verdict.json", "/vertaling.en.json"):
                return self.stuur(map_ / pad[1:], "application/json; charset=utf-8")
            self.send_error(404)

        def stuur(self, bestand, soort):
            if not bestand.exists():
                return self.send_error(404)
            self.stuur_data(bestand.read_bytes(), soort)

        def stuur_data(self, data, soort):
            self.send_response(200)
            self.send_header("Content-Type", soort)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Debatviewer op {url}, podium op {url}podium, arcade op {url}arcade (leest {map_}). Stop met Ctrl+C.")
    if not os.getenv("VIEWER_STIL"):
        webbrowser.open(url + "podium")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
