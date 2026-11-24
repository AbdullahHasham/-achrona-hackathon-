"""Lekcontrole: staat er inhoud uit de Kennisbronnen in onze prompts, code of documenten?

Spelregel: prompts en code bevatten geen inhoud uit de Kennisbronnen (geen record-ID's of feiten).
Start met `uv run python lek.py`. Leest kennisbronnen/<rol>/ (uit pack.zip) en vergelijkt met alles wat
git zou meenemen, behalve de uitvoer van een run. Toont alleen waar het lekt, nooit de inhoud zelf.
"""
import re
import subprocess
import sys
from pathlib import Path

import bewijs

ROOT = Path(__file__).parent
UITVOER = {"trace.json", "verdict.json"}  # hier hoort de inhoud uit het debat net wél
KIT = ("schema/", "voorbeeld/", "validate.py", "uv.lock", "LICENSE")  # ongewijzigd van de organisatie
SOORTEN = {".py", ".md", ".html", ".js", ".css", ".toml", ".json", ".example", ".txt"}
WOORDEN = 5  # zoveel opeenvolgende woorden gelijk = overgenomen tekst


def woorden(tekst):
    return re.findall(r"[a-zà-ÿ0-9]+", tekst.lower())


def reeksen(tekst):
    w = woorden(tekst)
    return {tuple(w[i:i + WOORDEN]) for i in range(len(w) - WOORDEN + 1)}


def bestanden():
    uit = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    for naam in filter(None, uit.split("\0")):
        pad = ROOT / naam
        if pad.name not in UITVOER and not naam.startswith(KIT) and pad.suffix in SOORTEN and pad.exists():
            yield naam, pad.read_text(encoding="utf-8", errors="replace")


def zoek(bron, teksten, publiek=""):
    """Geeft [(bestand, wat)]. `publiek` = tekst die iedereen kreeg (Rolkaarten): die mag."""
    bron_ids, bron_reeksen = set(bewijs.ids(bron)), reeksen(bron) - reeksen(publiek)
    gevonden = []
    for naam, tekst in teksten:
        fout_ids = [i for i in bewijs.ids(tekst) if i in bron_ids]
        if fout_ids:
            gevonden.append((naam, f"{len(fout_ids)} record-ID('s) uit de Kennisbronnen: {', '.join(fout_ids)}"))
        overlap = reeksen(tekst) & bron_reeksen
        if overlap:
            regels = [n for n, r in enumerate(tekst.splitlines(), 1) if reeksen(r) & bron_reeksen]
            gevonden.append((naam, f"{len(overlap)} woordreeks(en) gelijk aan een Kennisbron, regel {regels[:8] or '?'}"))
    return gevonden


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    paden = [p for p in (ROOT / "kennisbronnen").glob("*/*") if p.suffix in (".md", ".csv")]
    if not paden:
        sys.exit("Geen Kennisbronnen in kennisbronnen/<rol>/. Pak eerst pack.zip uit.")
    bron = "\n".join(p.read_text(encoding="utf-8") for p in paden)
    publiek = (ROOT / "rolkaarten.md").read_text(encoding="utf-8")
    gevonden = zoek(bron, bestanden(), publiek)
    for naam, wat in gevonden:
        print(f"LEK {naam}: {wat}")
    print(f"ROOD: {len(gevonden)} mogelijk(e) lek(ken)." if gevonden else f"GROEN: geen inhoud van {len(paden)} bronbestanden in prompts, code of documenten.")
    sys.exit(1 if gevonden else 0)


if __name__ == "__main__":
    main()
