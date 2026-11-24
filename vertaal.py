"""Engelse weergave van een run, voor het podium (knop NL/EN).

Start met `uv run python vertaal.py [map]` (na een run) of `uv run python vertaal.py [map] --volg` (tijdens een run).
Schrijft vertaling.en.json naast trace.json. trace.json en verdict.json blijven ongewijzigd: de vertaling is
alleen weergave. Een vertaling telt pas als alle record-ID's en getallen erin dezelfde zijn als in het origineel.
"""
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

import bewijs
from council import ROOT, schrijf_json
from koning import json_uit

PER_KEER = 5000  # tekens per vertaalvraag
TEGELIJK = 4

OPDRACHT = """You translate the minutes of a debate from Dutch to English for an audience that does not read Dutch.
- Translate faithfully: add nothing, leave nothing out, keep doubts and hedges as they are.
- Keep every record ID (like XXX-000) exactly, and every number, day and clock time in digits as in the Dutch.
- Keep line breaks, lists and **bold** markers.
- Fixed terms: Koning = King, Raadslid = Council member, Rol = Role, Kennisbron = source, ingreep = intervention,
  mechanisme = mechanism, verloop = pattern over time, stand = status, onbekend = unknown, vermoeden = suspected, getoetst = tested.
- Names of things in this world get one fixed English name. Use the glossary; add new ones to "glossary".

Answer with only this JSON object:
{"translations": {"<number>": "<English text>"}, "glossary": {"<Dutch name>": "<English name>"}}"""


def verzamel(trace, verdict=None):
    """Alle teksten van een run die het podium toont, elk één keer, in volgorde."""
    uit = {}

    def neem(*teksten):
        for t in teksten:
            if isinstance(t, str) and t.strip():
                uit.setdefault(t)

    for b in trace.get("beurten") or []:
        neem(b.get("vraag"), b.get("reden"), b.get("hypothese"), *(b.get("open_vragen") or []), *(b.get("uitdaging") or []), b.get("tekst"))
        for v in b.get("verklaringen") or []:
            neem(v.get("naam"))
    for g in trace.get("geschillen") or []:
        neem(g.get("onderwerp"), *(g.get("standpunten") or {}).values(), g.get("uitkomst"))
    if verdict:
        neem(verdict.get("samenvatting"), *((o or {}).get("bewering") for o in (verdict.get("oorzaak") or {}).values()))
        for a in verdict.get("acties") or []:
            neem(a.get("actie"), a.get("reden"))
        for r in verdict.get("risicos") or []:
            neem(r.get("risico"), r.get("mitigatie"))
        neem((verdict.get("voorspelling") or {}).get("bewering"))
    return list(uit)


def getallen(tekst):
    zonder_ids = bewijs.ID_RE.sub(" ", tekst)
    return Counter(n.replace(",", ".").rstrip(".") for n in re.findall(r"\d+(?:[.,:]\d+)*", zonder_ids))


def klopt(origineel, vertaald):
    """Dezelfde record-ID's en geen enkel getal kwijt. Extra cijfers mogen: 'vier' kan '4' worden."""
    return bool(vertaald and vertaald.strip()) and set(bewijs.ids(origineel)) == set(bewijs.ids(vertaald)) and not getallen(origineel) - getallen(vertaald)


def in_porties(teksten):
    portie, lengte = [], 0
    for t in teksten:
        if portie and lengte + len(t) > PER_KEER:
            yield portie
            portie, lengte = [], 0
        portie.append(t)
        lengte += len(t)
    if portie:
        yield portie


class Vertaler:
    def __init__(self, map_):
        load_dotenv(ROOT / ".env")
        self.map, self.doel = map_, map_ / "vertaling.en.json"
        self.client = AsyncOpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"], max_retries=6, timeout=120)
        self.data = {"run_id": None, "teksten": {}, "woordenlijst": {}}
        self.mislukt = Counter()

    def lees(self, naam):
        try:
            return json.loads((self.map / naam).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    async def portie(self, teksten):
        vraag = (f"Glossary so far:\n{json.dumps(self.data['woordenlijst'], ensure_ascii=False)}\n\nTranslate:\n"
                 + json.dumps({str(n): t for n, t in enumerate(teksten)}, ensure_ascii=False, indent=1))
        try:
            r = await self.client.chat.completions.create(
                model=os.environ["MODEL_KLEIN"], messages=[{"role": "system", "content": OPDRACHT}, {"role": "user", "content": vraag}])
            antwoord = json_uit(r.choices[0].message.content)
        except Exception as e:
            print(f"LET OP: vertaalvraag mislukte ({type(e).__name__}: {str(e)[:120]})")
            antwoord = {}
        vertalingen = antwoord.get("translations") if isinstance(antwoord.get("translations"), dict) else {}
        for nl, en in (antwoord.get("glossary") or {}).items() if isinstance(antwoord.get("glossary"), dict) else ():
            if isinstance(en, str):
                self.data["woordenlijst"].setdefault(nl, en)
        for n, t in enumerate(teksten):
            en = vertalingen.get(str(n))
            en = bewijs.normaliseer(en) if isinstance(en, str) else None
            if klopt(t, en):
                self.data["teksten"][t] = en
            else:
                self.mislukt[t] += 1
        schrijf_json(self.doel, self.data)

    async def ronde(self):
        """Vertaal wat nog niet vertaald is. Geeft True als de run klaar is en alles gedaan is."""
        trace = self.lees("trace.json")
        if not trace:
            return False
        if trace.get("run_id") != self.data["run_id"]:
            oud = self.lees("vertaling.en.json")
            zelfde = oud and oud.get("run_id") == trace.get("run_id")
            self.data = oud if zelfde else {"run_id": trace.get("run_id"), "teksten": {}, "woordenlijst": {}}
            self.mislukt.clear()
        verdict = self.lees("verdict.json")
        verdict = verdict if verdict and verdict.get("run_id") == trace.get("run_id") else None
        nodig = [t for t in verzamel(trace, verdict) if t not in self.data["teksten"] and self.mislukt[t] < 3]
        # een tekst die eerder mislukte gaat alleen, zodat een andere tekst hem niet in de war brengt
        porties = [[t] for t in nodig if self.mislukt[t]] + list(in_porties([t for t in nodig if not self.mislukt[t]]))
        if porties and not self.data["woordenlijst"]:
            await self.portie(porties.pop(0))  # eerst één portie, zodat de woordenlijst vastligt
        for i in range(0, len(porties), TEGELIJK):
            await asyncio.gather(*(self.portie(p) for p in porties[i:i + TEGELIJK]))
        if not porties and not nodig:
            return trace.get("status") != "bezig"
        return False


async def run(map_, volg):
    v = Vertaler(map_)
    while True:
        klaar = await v.ronde()
        if klaar and not volg:
            break
        if not v.lees("trace.json") and not volg:
            sys.exit(f"Geen trace.json in {map_}.")
        await asyncio.sleep(3 if volg else 0)
    alle = verzamel(v.lees("trace.json"), v.lees("verdict.json"))
    gedaan = sum(t in v.data["teksten"] for t in alle)
    print(f"Vertaald: {gedaan} van {len(alle)} teksten. {len(alle) - gedaan} blijven in het Nederlands omdat een ID of getal niet klopte.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    argumenten = [a for a in sys.argv[1:] if not a.startswith("--")]
    try:
        asyncio.run(run(Path(argumenten[0]).resolve() if argumenten else ROOT, "--volg" in sys.argv))
    except KeyboardInterrupt:
        pass
