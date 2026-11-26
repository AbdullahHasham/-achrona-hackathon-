"""Automated zero-leakage check: verifies no knowledge-base IDs or phrases appear in code/prompts.

Rule: prompts and code must not contain any content from the Knowledge Sources
(no record IDs or facts). Everything the Council knows must come from the debate.

Run with `python lek.py`. Compares `kennisbronnen/<rol>/*` against all git-tracked
source/prompt/doc files (excluding generated run outputs `trace.json`, `verdict.json`,
and `runs/`). Prints only where a leak occurs, never source content itself.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from verify_gate import extract_ids

ROOT = Path(__file__).parent
UITVOER_NAMES = {"trace.json", "verdict.json"}
UITVOER_PREFIXES = ("runs/",)
KIT_PREFIXES = ("schema/", "voorbeeld/", "validate.py", "uv.lock", "LICENSE", "README.md")
EXTS = {".py", ".md", ".html", ".js", ".css", ".toml", ".json", ".example", ".txt"}
NGRAM_LEN = 5


def woorden(tekst: str) -> list[str]:
    return re.findall(r"[a-zà-ÿ0-9]+", tekst.lower())


def reeksen(tekst: str) -> set[tuple[str, ...]]:
    w = woorden(tekst)
    return {tuple(w[i : i + NGRAM_LEN]) for i in range(len(w) - NGRAM_LEN + 1)}


def bestanden():
    uit = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    for naam in filter(None, uit.split("\0")):
        pad = ROOT / naam
        if (
            pad.name not in UITVOER_NAMES
            and not naam.startswith(UITVOER_PREFIXES)
            and not naam.startswith(KIT_PREFIXES)
            and pad.suffix in EXTS
            and pad.exists()
        ):
            yield naam, pad.read_text(encoding="utf-8", errors="replace")


def zoek(bron: str, teksten, publiek: str = "") -> list[tuple[str, str]]:
    bron_ids = set(extract_ids(bron))
    bron_reeksen = reeksen(bron) - reeksen(publiek)
    gevonden: list[tuple[str, str]] = []
    for naam, tekst in teksten:
        fout_ids = sorted(i for i in extract_ids(tekst) if i in bron_ids)
        if fout_ids:
            gevonden.append((naam, f"{len(fout_ids)} KB record ID(s): {', '.join(fout_ids)}"))
        overlap = reeksen(tekst) & bron_reeksen
        if overlap:
            regels = [n for n, r in enumerate(tekst.splitlines(), 1) if reeksen(r) & bron_reeksen]
            gevonden.append((naam, f"{len(overlap)} matching phrase(s) from KB at line(s) {regels[:8] or '?'}"))
    return gevonden


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    paden = sorted(p for p in (ROOT / "kennisbronnen").glob("*/*") if p.suffix in (".md", ".csv"))
    if not paden:
        sys.exit("No knowledge sources found in kennisbronnen/<rol>/. Extract pack.zip first.")
    bron = "\n".join(p.read_text(encoding="utf-8") for p in paden)
    publiek = (ROOT / "rolkaarten.md").read_text(encoding="utf-8") if (ROOT / "rolkaarten.md").exists() else ""
    gevonden = zoek(bron, bestanden(), publiek)
    for naam, wat in gevonden:
        print(f"LEK {naam}: {wat}")
    if gevonden:
        print(f"ROOD: {len(gevonden)} potential leak(s) found.")
        sys.exit(1)
    print(f"GROEN: zero content from {len(paden)} knowledge-source files found in prompts, code, or docs.")


if __name__ == "__main__":
    main()
