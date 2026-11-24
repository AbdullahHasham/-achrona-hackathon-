"""Vergelijking voor de pitch: één agent die alles tegelijk leest, met hetzelfde model en hetzelfde Verdict-formaat.

Start met `uv run python basislijn.py` (na een Council-run, met de bestanden uit pack.zip in kennisbronnen/).
Dit is GEEN deel van de Council: het schrijft geen trace.json en geen verdict.json in de root, en geen enkel
Raadslid of de Koning ziet hier iets van. Het resultaat komt in runs/basislijn_<tijd>/.
"""
import asyncio
import json
import os
import sys
from datetime import datetime

from agent_framework import Agent
from dotenv import load_dotenv

import bewijs
import raadsleden
from council import ROOT, client, controleer, kennisbron_bestanden, met_geduld, schrijf_json
from koning import SLOTS, VERDICT, json_uit

INSTRUCTIE = """Je onderzoekt de oorzaak van de Desync in Achrona. Je krijgt alle Kennisbronnen tegelijk.
Baseer je alleen op deze bronnen en noem record-ID's precies zoals ze er staan. Verzin nooit een ID."""


def meet(verdict, bestaand):
    """Wat zonder modelantwoord te tellen valt: bestaan de ID's, en steunt elk deel op meer dan één Rol?"""
    genoemd = bewijs.ids(json.dumps(verdict, ensure_ascii=False))
    uit = {"ids": len(genoemd), "verzonnen_ids": [i for i in genoemd if i not in bestaand], "rollen_per_deel": {}, "zekerheid": {}}
    for s in SLOTS:
        stuk = (verdict.get("oorzaak") or {}).get(s) or {}
        uit["rollen_per_deel"][s] = len({bewijs.eigenaar(i) for i in bewijs.ids(" ".join(map(str, stuk.get("bewijs") or [])))})
        uit["zekerheid"][s] = stuk.get("zekerheid")
    uit["acties"] = len(verdict.get("acties") or [])
    uit["geschillen"] = len(verdict.get("geschillen") or [])
    return uit


async def run():
    load_dotenv(ROOT / ".env")
    leden = raadsleden.alle()
    controleer(leden)
    stukken = [(l, kennisbron_bestanden(l.ROL)) for l in leden]
    if not all(b for _, b in stukken):
        sys.exit("Pak eerst pack.zip uit: deze vergelijking leest de bestanden in kennisbronnen/<rol>/.")
    bron = "\n\n".join(f"# {l.NAAM}\n\n" + "\n\n".join(f"## {p.name}\n\n{p.read_text(encoding='utf-8')}" for p in b) for l, b in stukken)
    bestaand = set(bewijs.ids(bron))
    opties = {"reasoning_effort": os.environ["REASONING"]} if os.getenv("REASONING") else {}
    agent = Agent(client("MODEL_GROOT"), INSTRUCTIE, name="basislijn")
    prompt = f"{bron}\n\n{VERDICT.format(toegelaten='- elk ID uit de bronnen hierboven')}".replace("uit het debat", "uit de bronnen")
    r = await met_geduld(lambda: agent.run(prompt, options=opties))
    verdict = json_uit(bewijs.normaliseer(r.text))

    uit = ROOT / "runs" / f"basislijn_{datetime.now():%Y%m%dT%H%M%S}"
    uit.mkdir(parents=True, exist_ok=True)
    schrijf_json(uit / "verdict.json", verdict)
    print(f"Eén agent: {uit.relative_to(ROOT)}\n  {json.dumps(meet(verdict, bestaand), ensure_ascii=False)}\n  tokens: {r.usage_details}")
    for s in SLOTS:
        print(f"  {s}: {((verdict.get('oorzaak') or {}).get(s) or {}).get('bewering')}")
    if (ROOT / "verdict.json").exists() and (ROOT / "trace.json").exists():
        council_verdict = json.loads((ROOT / "verdict.json").read_text(encoding="utf-8"))
        trace = json.loads((ROOT / "trace.json").read_text(encoding="utf-8"))
        m = meet(council_verdict, bestaand)
        m["geschillen"] = len(trace.get("geschillen") or [])
        print(f"Council:\n  {json.dumps(m, ensure_ascii=False)}\n  tokens: {trace.get('verbruik')}")
        for s in SLOTS:
            print(f"  {s}: {council_verdict['oorzaak'][s]['bewering']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run())
