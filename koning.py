"""De Koning: leest het debat, kiest wie spreekt en waarom, stopt zelf, en stelt het Verdict op.

De Koning is een agent (MODEL_GROOT) met regels in code eromheen:
  - de agent kiest, omdat hij leest wat er gezegd is;
  - code telt beurten, kent de open sporen (ID's) en toetst het Verdict aan het schema en aan het debat.
Hij heeft geen Kennisbron en geen tools. Alles wat hij weet, komt uit het debat.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

import bewijs

ROOT = Path(__file__).parent
SLOTS = ("ingreep", "mechanisme", "verloop")
STANDEN = ("onbekend", "vermoeden", "getoetst")
WERKPLAN = 9  # de Koning plant met 9 beurten: hij stopt zelf, niet het Beurtenbudget
# Eén getuige is nooit zekerder dan dit; twee onafhankelijke Rollen mogen hoger.
PLAFOND_EEN_ROL = 0.6
PLAFOND_TWEE_ROLLEN = 0.85

INSTRUCTIE = """Je bent de Koning van de Council of Achrona. Je leidt het debat over de oorzaak van de Desync.
Je hebt geen Kennisbron: je weet alleen wat de Raadsleden in dit debat inbrengen. Verzin niets en vul niets aan met eigen kennis.

De Council moet vinden:
- ingreep: wie deed wat, en wanneer precies. Een ingreep is een handeling of een verandering door iemand, vóór de symptomen. Een storing, uitval of melding is een symptoom en hoort bij het mechanisme;
- mechanisme: hoe die ingreep stap voor stap tot de symptomen leidt;
- verloop: het patroon in de tijd (dagen, aantallen, regelmaat) en wanneer het opnieuw toeslaat;
- een herstelplan in de juiste volgorde, met de risico's ervan."""

KIES = """Beslis nu wie spreekt, of stop het debat.

Zo leid je goed:
1. Houd een bord bij met 2 of 3 verklaringen die elk een handeling als begin hebben. Zet bij elke hoogstens 3 punten voor en 3 tegen, elk een record-ID en hoogstens 10 woorden. Alleen een record weerlegt een verklaring. Leidend is de verklaring die de meeste symptomen van verschillende Rollen verklaart, niet de eerste die genoemd werd.
2. Begin breed: vraag over de hele periode wat er werkt, wat er veranderde en welke storingen er staan. Het begin van de Desync is waar symptomen van meerdere Rollen samenvallen, niet de eerste melding in één bron. Vraag pas naar smalle tijdvensters als de volledige reeks bekend is.
3. Kies de vraag die de verklaringen het best van elkaar scheidt: als verklaring X klopt, wat moet deze bron dan tonen, en wanneer? Een vraag die je leidende verklaring kan omverwerpen is beter dan een die ze herhaalt.
4. Een bewering van één Rol is een vermoeden. Ze is getoetst als een andere Rol ze uit een eigen bron draagt. Verwijst een bron naar een andere Rol, volg dan dat spoor.
5. Elke Rol ziet alleen haar eigen bron. Vraag een Rol nooit om een record van een andere Rol te bevestigen; vraag wat haar eigen bron zegt over hetzelfde moment, dezelfde plaats, persoon of zaak. Records uit verschillende bronnen verbinden (zelfde dag, uur, naam, getal of woorden) is jouw werk.
6. Kruis-zoeken: geef bij de vraag 2 tot 6 zoektermen mee uit getuigenissen van ándere Rollen: namen, zaken, dagen of letterlijke woorden. De Rol zoekt ze op in haar eigen bron. Zo vind je wat een Rol uit zichzelf niet zou noemen.
7. Elke Rol heeft een Belang en niet elke conclusie klopt, ook niet van iemand met gezag. Een meting of logregel blijft bewijs: het Belang maakt de conclusie verdacht, niet de waarneming. Samenvallen in de tijd is nog geen oorzaak, maar wat vlak vóór een verandering gebeurde is een kandidaat.
8. Leg de reeksen van verschillende Rollen naast elkaar. Reken zelf na: verschillen, perioden, verhoudingen, eenheden. Kijk waar ze samenvallen, waar ze beginnen en waar ze veranderen.
9. Zoek de gemeenschappelijke afhankelijkheid: wat hebben alle getroffen systemen gemeen? Eén oorzaak die elk symptoom apart verklaart is sterker dan een losse verklaring per symptoom.
10. Leg een tegenspraak met de record-ID's voor aan wie ze kan beslechten.
11. Blijf niet hangen bij één vraag: wie het deed, hoe het werkt en wanneer het terugkomt tellen even zwaar. Een meting die volgens de Rol niet bestaat, komt er niet door ze opnieuw te vragen: noteer ze als grens van het bewijs.
12. Verdeel de beurten over ingreep, mechanisme, verloop en herstel. Een Rol die nog niet sprak brengt meestal meer dan een volgende vraag aan dezelfde Rol. Bracht een Rol in haar laatste beurt weinig nieuwe records, kies haar dan niet opnieuw zonder nieuw spoor.
13. Herstel: weet je welke voorwaarden, volgorde en waarschuwingen er zijn, en of Rollen elkaar daarin tegenspreken?
14. Een vraag heeft één doel, 2 of 3 concrete deelvragen en hoogstens 70 woorden. Vraag dagen, uren, getallen, regels en record-ID's. Vraag geen samenvatting.
15. Open vragen: schrap wat beantwoord is en wat volgens de Rollen niemand kan beantwoorden. Houd alleen over wat het Verdict nog kan veranderen.
16. Stand: 'vermoeden' = één Rol draagt het met records, 'getoetst' = minstens twee Rollen dragen het met eigen records. Een stand zakt alleen als een record hem weerlegt.
17. Jij beslist wanneer het genoeg is, niet het budget. Stop zodra de drie delen getoetst zijn en het herstel bekend is, of zodra geen enkele vraag het Verdict nog kan veranderen. Beurt 10 is een noodreserve.

Antwoord met alleen dit JSON-object:
{
  "verklaringen": [{"naam": "de handeling, in hoogstens 8 woorden", "voor": ["XXX-000: waarom"], "tegen": ["XXX-000: waarom"], "stand": "leidend|open|weerlegd"}],
  "hypothese": "je leidende verklaring in 2 tot 4 zinnen: ingreep, mechanisme stap voor stap, patroon; en welke schakel het zwakst is",
  "stand": {"ingreep": "onbekend|vermoeden|getoetst", "mechanisme": "...", "verloop": "..."},
  "open_vragen": ["hoogstens 5"],
  "toetst": ["rollen wiens bewering je met deze vraag laat toetsen, leeg als het om iets nieuws gaat"],
  "rol": "archmage|engineer|guardian_keeper|historian|oracle|beast_master of null om te stoppen",
  "vraag": "de vraag aan die Rol, of null",
  "zoektermen": ["2 tot 6 termen uit getuigenissen van andere Rollen, leeg bij de eerste vraag of als je stopt"],
  "reden": "waarom deze Rol en deze vraag nu (of waarom je stopt): noem concreet welke verklaringen je scheidt of welke bewering, welk record of welke schakel je toetst. Hoogstens 60 woorden."
}"""

UITDAAG = """De Koning wil het debat nu sluiten. Zijn reden: {reden}

Jij bent de Uitdager. Probeer de conclusie onderuit te halen vóór het debat sluit. Zoek in het debat, en alleen daar:
- een waarneming die de leidende verklaring niet verklaart;
- een andere verklaring die nog door geen record weerlegd is;
- een bewering van één Rol waarop veel steunt en die een andere Rol nog kan toetsen;
- uitspraken van verschillende Rollen over hetzelfde die niemand naast elkaar legde;
- een stap van het herstel waarvan voorwaarde, volgorde of waarschuwing onbekend is.
Noem een gat alleen als een Rol het met haar eigen soort bron nog kan vullen. Zei een Rol al dat haar bron iets niet bevat, dan is dat geen gat maar een grens van het bewijs: noem het niet. Weeg of het gat het Verdict zou veranderen, niet of er nog iets te meten valt.

Antwoord met alleen dit JSON-object, met hoogstens 2 gaten, het ernstigste eerst. Geen gaten: lege lijst.
{{"gaten": [{{"gat": "wat ontbreekt en waarom het telt", "rol": "wie het kan vullen", "vraag": "de vraag"}}]}}"""

OORDEEL = """Het debat is gesloten. Weeg het bewijs voor je het Verdict schrijft, in hoogstens 500 woorden:
1. Loop je bord met verklaringen af. Welke waarnemingen van welke Rollen verklaart elke verklaring, en welk record spreekt haar tegen? Kies.
2. Verbind de bronnen: waar noemen verschillende Rollen dezelfde dag, hetzelfde uur, dezelfde zaak, hetzelfde getal of dezelfde woorden? Wat volgt daaruit dat geen enkele Rol alleen kon zeggen?
3. Het mechanisme: wat hebben alle getroffen systemen gemeen? Schrijf de keten stap voor stap, met per stap het record, en verklaar elk symptoom apart met diezelfde oorzaak. Reken na of de getallen bij elkaar passen.
4. Het patroon: schrijf de volledige reeks dagen, uren en waarden uit. Wanneer begint ze, waar verandert ze, en wat gebeurde er vlak daarvoor? Reken voor wanneer het opnieuw toeslaat als niemand ingrijpt.
5. De tegenspraken: wie zei wat, en welk record beslecht het? Welke conclusie van een Rol bleek fout?
6. Het herstel in volgorde: wat stopt de schade eerst, wat moet daarvoor klaar zijn, wat mag zeker niet, wie wordt geschaad?
7. Wat weet de Council niet?
Baseer je alleen op wat in het debat gezegd is."""

TEGENLEES = """Lees je eigen Verdict nu kritisch tegen het debat, alsof een ander het schreef:
- Klopt elke dag, elk uur, elke naam en elk getal met wat er gezegd werd?
- Draagt elk record zijn bewering echt? Is er in het debat een sterker of onafhankelijker record van een andere Rol? Wissel dan.
- Verklaart het mechanisme álle belangrijke waarnemingen, en is elke stap uitgesproken? Vul een ontbrekende stap aan als het debat hem draagt.
- Volgt het herstelplan elke voorwaarde, volgorde en waarschuwing uit het debat? Staat wat de schade stopt vooraan? Is elke stap concreet en uitvoerbaar?
- Staat er nog voorbehoud in een bewering dat in zekerheid hoort?
{klachten}
Antwoord met alleen het volledige, verbeterde JSON-object in dezelfde vorm."""

VERDICT = """Schrijf nu het Council Verdict. Gebruik alleen feiten uit het debat en alleen deze record-ID's als bewijs:
{toegelaten}

Regels:
- ingreep is de handeling waarmee het begon en wie ze deed, geen symptoom. mechanisme legt uit hoe die handeling tot elk symptoom leidt. verloop is de reeks in de tijd.
- Elk deel van de oorzaak staat op zichzelf: 2 tot 4 duidelijke zinnen, in tijdsvolgorde, met alle betrokkenen, tijdstippen, getallen en veranderingen die het debat met records draagt. Kies de verklaring die de meeste onafhankelijke waarnemingen verklaart en schrijf ze beslist op; je twijfel zet je in zekerheid, niet in woorden als 'mogelijk', 'hypothese' of 'niet getoetst'. Zet een deel alleen op null als geen enkel record een stap ervan draagt; draagt het debat het gedeeltelijk, schrijf dan wat gedragen is en geef een lage zekerheid.
- bewijs: de records die de bewering echt dragen, hoogstens 10. Loop de bewijslijst hierboven na en kies records van zoveel verschillende Rollen als het debat geeft. Elk ID dat je in de bewering noemt, staat ook in bewijs. Geen opvulling.
- zekerheid (0-1): 0.9 = meerdere onafhankelijke bronnen en geen tegenspraak; 0.6 = één bron of een open tegenspraak; 0.3 = een vermoeden.
- acties: het herstelplan in uitvoeringsvolgorde, 4 tot 7 stappen. Eerst wat verdere schade stopt, dan de oorzaak wegnemen, dan herstellen, dan controleren. Gebruik elke voorwaarde, volgorde en waarschuwing voor het herstel uit het debat. Zeg per actie wie ze uitvoert. Het Verdict is een beslissing op de best gedragen verklaring: stel het herstel niet uit tot alles gemeten is. Onderzoek is alleen een actie als het herstel zonder die stap onveilig is.
- risicos: wat kan misgaan bij het herstel, ook de verleidelijke maar verkeerde oplossing, en wie er schade van heeft.
- voorspelling: de dag (geheel getal) waarop de Desync opnieuw toeslaat als niemand ingrijpt, met de berekening uit het patroon. Laat het veld weg als het debat dat niet onderbouwt.
- geschillen: elke echte tegenspraak en elke verworpen verklaring, met het standpunt per Rol, hoe ze beslecht werd en met welk bewijs. Geef een Rol alleen een standpunt dat zij zelf in het debat innam; 'geen record van' is iets anders dan 'ontkend'. Niet beslecht: uitkomst null.

Antwoord met alleen dit JSON-object:
{{
  "samenvatting": "3 tot 5 zinnen",
  "oorzaak": {{
    "ingreep": {{"bewering": "...", "bewijs": ["XXX-000"], "zekerheid": 0.0}},
    "mechanisme": {{"bewering": "...", "bewijs": [], "zekerheid": 0.0}},
    "verloop": {{"bewering": "...", "bewijs": [], "zekerheid": 0.0}}
  }},
  "acties": [{{"actie": "...", "reden": "...", "bewijs": []}}],
  "risicos": [{{"risico": "...", "mitigatie": "..."}}],
  "voorspelling": {{"dag": 1, "bewering": "...", "bewijs": []}},
  "geschillen": [{{"onderwerp": "...", "standpunten": {{"rol": "standpunt"}}, "uitkomst": "... of null", "bewijs": []}}]
}}"""


@dataclass
class Keuze:
    rol: str | None
    vraag: str | None
    reden: str
    extra: dict = field(default_factory=dict)  # verklaringen, hypothese, stand, open_vragen, ...: gaan mee in de routering


def json_uit(tekst):
    """Het eerste JSON-object in een antwoord. Modellen zetten soms tekst of ``` rond hun JSON."""
    lezer = json.JSONDecoder(strict=False)
    start = (tekst or "").find("{")
    while start != -1:
        try:
            data, _ = lezer.raw_decode(tekst, start)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        start = tekst.find("{", start + 1)
    raise ValueError("geen JSON-object in het antwoord")


def debat_tekst(beurten, namen):
    """Het debat zoals het gevoerd is: de vragen van de Koning en de antwoorden van de Raadsleden."""
    regels = []
    for b in beurten:
        if b["soort"] == "routering" and b["aan"]:
            regels.append(f"[Koning vraagt {namen.get(b['aan'], b['aan'])}] {b['vraag']}")
        elif b["soort"] == "bericht" and b["spreker"] in namen:
            regels.append(f"[{namen[b['spreker']]}] {b['tekst']}")
    return "\n\n".join(regels) or "(nog niets gezegd)"


def dossier(beurten, leden, budget):
    """Wat code zeker weet: beurten, wie al sprak, open sporen en verdachte ID's. Geen inhoud uit bronnen."""
    namen = {l.ROL: l.NAAM for l in leden}
    sprak = [b["spreker"] for b in bewijs.berichten(beurten)]
    regels = [f"Raadslid-beurten gebruikt: {len(sprak)} van {budget}. Nog over in je werkplan van {WERKPLAN}: {max(0, WERKPLAN - len(sprak))}.", "", "Raadsleden:"]
    opbrengst = bewijs.opbrengst(beurten)
    for l in leden:
        eigen = [n for rol, n in opbrengst if rol == l.ROL]
        regels.append(f"- {l.ROL} ({l.NAAM}): {l.PERSPECTIEF} Belang: {l.BELANG}. "
                      + (f"Sprak {len(eigen)} keer; nieuwe records per beurt: {', '.join(map(str, eigen))}." if eigen else "Sprak nog niet."))
    sporen = bewijs.open_sporen(beurten)
    if sporen:
        regels += ["", "Open sporen (een bron verwijst naar dit record, maar de eigen Rol bracht het nog niet in):"]
        regels += [f"- {i}, genoemd door {', '.join(namen[r] for r in dict.fromkeys(rollen))}; na te gaan bij {bewijs.eigenaar(i)}"
                   for i, rollen in sporen.items()]
    verdacht = bewijs.twijfelachtig(beurten)
    if verdacht:
        regels += ["", "Niet teruggevonden in de bron van de spreker, dus geen bewijs: " + ", ".join(verdacht)]
    vorige = next((b for b in reversed(beurten) if b["soort"] == "routering" and b.get("stand")), None)
    if vorige:
        regels += ["", "Jouw notities van vóór de laatste beurt:", "Stand: " + ", ".join(f"{k} {v}" for k, v in vorige["stand"].items())]
        regels += [f"Hypothese: {vorige['hypothese']}"] if vorige.get("hypothese") else []
        for v in vorige.get("verklaringen") or []:
            regels.append(f"Verklaring '{v['naam']}' ({v['stand']}). Voor: {'; '.join(v['voor']) or 'niets'}. Tegen: {'; '.join(v['tegen']) or 'niets'}.")
        regels += ["Open vragen:"] + [f"- {v}" for v in vorige.get("open_vragen") or ["(geen)"]]
    return "\n".join(regels)


def _regels(x, hoogstens, lengte=240):
    return [bewijs.normaliseer(str(v).strip())[:lengte] for v in (x if isinstance(x, list) else []) if str(v).strip()][:hoogstens]


def lees_bord(x):
    bord = []
    for v in x if isinstance(x, list) else []:
        if isinstance(v, dict) and str(v.get("naam") or "").strip():
            stand = str(v.get("stand") or "").strip().lower()
            bord.append({"naam": bewijs.normaliseer(str(v["naam"]).strip())[:120], "voor": _regels(v.get("voor"), 3), "tegen": _regels(v.get("tegen"), 3),
                         "stand": stand if stand in ("leidend", "open", "weerlegd") else "open"})
    return bord[:3]


def lees_keuze(data, leden):
    """Maak van het antwoord van de Koning een geldige Keuze, of geef een ValueError met uitleg."""
    rollen = {l.ROL for l in leden}
    rol = data.get("rol")
    rol = None if rol in (None, "", "null", "None") else str(rol).strip().lower().replace(" ", "_")
    reden = bewijs.normaliseer(str(data.get("reden") or "").strip())
    vraag = bewijs.normaliseer(str(data.get("vraag") or "").strip()) if rol else None
    if rol and rol not in rollen:
        raise ValueError(f"rol {rol!r} bestaat niet; kies uit {', '.join(sorted(rollen))} of null")
    if not reden:
        raise ValueError("reden ontbreekt")
    if rol and not vraag:
        raise ValueError("vraag ontbreekt")
    stand = data.get("stand") if isinstance(data.get("stand"), dict) else {}
    open_vragen = data.get("open_vragen") if isinstance(data.get("open_vragen"), list) else []
    toetst = data.get("toetst") if isinstance(data.get("toetst"), list) else []
    return Keuze(rol, vraag, reden, {
        "verklaringen": lees_bord(data.get("verklaringen")),
        "zoektermen": _regels(data.get("zoektermen"), 6, 60) if rol else [],
        "hypothese": bewijs.normaliseer(str(data.get("hypothese") or "").strip()),
        "open_vragen": [bewijs.normaliseer(str(v).strip()) for v in open_vragen if str(v).strip()][:5],
        "stand": {s: stand.get(s) if stand.get(s) in STANDEN else "onbekend" for s in SLOTS},
        "toetst": [r for r in dict.fromkeys(str(t).strip().lower().replace(" ", "_") for t in toetst) if r in rollen and r != rol],
    })


def regel_keuze(beurten, leden, budget):
    """Noodregel als het model niet antwoordt: eerst een open spoor, dan wie nog niet sprak, anders stoppen."""
    sprak = [b["spreker"] for b in bewijs.berichten(beurten)]
    rollen = {l.ROL: l for l in leden}
    if len(sprak) < budget - 1:
        for i, door in bewijs.open_sporen(beurten).items():
            rol = bewijs.eigenaar(i)
            if rol in rollen and (not sprak or sprak[-1] != rol):
                return Keuze(rol, f"Er wordt verwezen naar {i} in jouw Kennisbron. Wat staat daar, en bevestigt of weerlegt het wat gezegd is?",
                             f"Noodregel (het model van de Koning antwoordde niet): open spoor {i}, genoemd door {', '.join(dict.fromkeys(door))}.")
        for rol, lid in rollen.items():
            if rol not in sprak:
                return Keuze(rol, "Wat zegt jouw Kennisbron over de ingreep, het mechanisme en het verloop van de Desync?",
                             f"Noodregel (het model van de Koning antwoordde niet): {lid.NAAM} sprak nog niet.")
    return Keuze(None, None, "Noodregel (het model van de Koning antwoordde niet): elke Rol is gehoord.")


class Koning:
    """`vraag_model(tekst)` is één call naar de Koning-agent en geeft zijn antwoord als tekst."""

    def __init__(self, vraag_model, leden, budget):
        self.vraag_model, self.leden, self.budget = vraag_model, leden, budget
        self.namen = {l.ROL: l.NAAM for l in leden}
        self.uitdagingen = 0

    async def _json(self, prompt, lees, pogingen=3):
        fout, tekst = None, prompt
        for _ in range(pogingen):
            try:
                return lees(json_uit(await self.vraag_model(tekst)))
            except ValueError as e:
                fout = e
                tekst = f"{prompt}\n\nJe vorige antwoord was niet bruikbaar: {e}. Antwoord met alleen het JSON-object."
        raise ValueError(f"de Koning gaf na {pogingen} pogingen geen bruikbaar antwoord: {fout}")

    def _context(self, beurten):
        return f"{dossier(beurten, self.leden, self.budget)}\n\nDEBAT TOT NU TOE\n\n{debat_tekst(beurten, self.namen)}"

    async def kies(self, beurten):
        prompt = f"{self._context(beurten)}\n\n{KIES}"
        sprak = {b["spreker"] for b in bewijs.berichten(beurten)}
        if len(sprak) == len(self.leden):
            prompt += ("\n\nElke Rol is nu gehoord. Maak eerst de synthese: lees per Rol 'Werking' en 'Reeks', en schrijf in de hypothese één volledige keten "
                       "die elk symptoom apart verklaart vanuit dezelfde verandering. Test daarna alleen de zwakste schakel, met wat een Rol wél kan geven "
                       "(een regel, een ontwerp, een reeks, een getal om mee te rekenen), niet met een meting die er niet is. Staat de keten, vraag dan naar het herstel of stop.")
        if len(bewijs.berichten(beurten)) >= WERKPLAN:
            prompt += ("\n\nJe werkplan is op. Stop nu (rol null) en leg in de reden uit wat vaststaat, op welk bewijs, en wat onbekend blijft. "
                       "Gebruik de noodreserve alleen als één concrete vraag het Verdict nog kan omkeren.")
        try:
            keuze = await self._json(prompt, lambda d: lees_keuze(d, self.leden))
            if keuze.rol is None and len(bewijs.berichten(beurten)) < WERKPLAN and self.uitdagingen < 1:
                keuze = await self._uitgedaagd(beurten, prompt, keuze)
            return keuze
        except Exception as e:  # een haperend model mag de run nooit breken
            print(f"LET OP: noodregel voor de Koning ({type(e).__name__}: {str(e)[:160]})")
            return regel_keuze(beurten, self.leden, self.budget)

    async def _uitgedaagd(self, beurten, prompt, keuze):
        """Vóór de Koning sluit, valt een Uitdager de conclusie aan. De Koning beslist daarna zelf: doorvragen of toch sluiten."""
        self.uitdagingen += 1
        rollen = {l.ROL for l in self.leden}
        try:
            data = await self._json(f"{self._context(beurten)}\n\n{UITDAAG.format(reden=keuze.reden)}", lambda d: d, pogingen=2)
            gaten = [g for g in data.get("gaten") or [] if isinstance(g, dict) and str(g.get("gat") or "").strip()
                     and str(g.get("rol") or "").strip().lower().replace(" ", "_") in rollen][:2]
        except Exception as e:
            print(f"LET OP: de Uitdager antwoordde niet ({type(e).__name__}: {str(e)[:120]})")
            return keuze
        if not gaten:
            keuze.extra["uitdaging"] = []
            return keuze
        tekst = "\n".join(f"- {g['gat']} (kan gevuld worden door {g['rol']}: {g.get('vraag') or ''})" for g in gaten)
        try:
            nieuw = await self._json(f"{prompt}\n\nJe wilde sluiten met deze reden: {keuze.reden}\nDe Uitdager noemt deze gaten:\n{tekst}\n"
                                     "Beslis opnieuw: geef een Rol het woord om het ernstigste gat te vullen, of sluit toch en leg in de reden uit waarom geen van deze gaten het Verdict verandert.",
                                     lambda d: lees_keuze(d, self.leden))
        except ValueError:
            nieuw = keuze
        nieuw.extra["uitdaging"] = [bewijs.normaliseer(str(g["gat"]).strip())[:300] for g in gaten]
        return nieuw

    async def oordeel(self, beurten):
        return bewijs.normaliseer(await self.vraag_model(f"{self._context(beurten)}\n\n{OORDEEL}"))

    async def verdict(self, beurten, oordeel, run_id):
        """Geeft (verdict, geschillen). Code toetst elk ID aan het debat; het model mag één keer verbeteren."""
        bekend = bewijs.ingebracht(beurten)
        toegelaten = "\n".join(f"- {i} ({bekend[i]}): {zin[:260]}" for i, zin in bewijs.bewijslijst(beurten).items()) or "- (geen)"
        prompt = f"{self._context(beurten)}\n\nJOUW OORDEEL\n\n{oordeel}\n\n{VERDICT.format(toegelaten=toegelaten)}"
        ruw = await self._json(prompt, lambda d: d)
        klachten = bezwaren(ruw, bekend)
        lijst = "Code vond deze fouten, herstel ze:\n" + "\n".join(f"- {k}" for k in klachten) if klachten else ""
        try:
            herzien = await self._json(f"{prompt}\n\nJE EERSTE VERSIE\n\n{json.dumps(ruw, ensure_ascii=False)}\n\n{TEGENLEES.format(klachten=lijst)}", lambda d: d)
            if gevuld(herzien, bekend) >= gevuld(ruw, bekend):
                ruw = herzien
            else:
                print("LET OP: de herziene versie van het Verdict was minder volledig, eerste versie blijft.")
        except Exception as e:
            print(f"LET OP: tegenlezing van het Verdict mislukt, eerste versie blijft ({type(e).__name__}: {str(e)[:160]})")
        return schoon(ruw, bekend, run_id)


def _lijst(x):
    return x if isinstance(x, list) else []


def _tekst(x):
    return bewijs.normaliseer(x.strip()) if isinstance(x, str) and x.strip() else None


def _bewijs(x, bekend):
    gevonden = bewijs.ids(" ".join(str(i) for i in _lijst(x)))
    return [i for i in gevonden if i in bekend]


def bezwaren(ruw, bekend):
    """Wat klopt niet aan deze versie van het Verdict? Enkel wat code zeker kan weten."""
    klachten = []
    oorzaak = ruw.get("oorzaak") if isinstance(ruw.get("oorzaak"), dict) else {}
    stukken = [(f"oorzaak.{s}", oorzaak.get(s) or {}) for s in SLOTS]
    stukken += [(f"acties[{n}]", a) for n, a in enumerate(_lijst(ruw.get("acties")), 1)]
    stukken += [("voorspelling", ruw["voorspelling"])] if isinstance(ruw.get("voorspelling"), dict) else []
    stukken += [(f"geschillen[{n}]", g) for n, g in enumerate(_lijst(ruw.get("geschillen")), 1)]
    for naam, stuk in stukken:
        if not isinstance(stuk, dict):
            klachten.append(f"{naam} is geen object")
            continue
        vreemd = [i for i in bewijs.ids(json.dumps(stuk, ensure_ascii=False)) if i not in bekend]
        if vreemd:
            klachten.append(f"{naam} steunt op records die hun eigen Rol niet inbracht: {', '.join(vreemd)}. Vervang of schrap ze.")
    for s in SLOTS:
        stuk = oorzaak.get(s) if isinstance(oorzaak.get(s), dict) else {}
        goed = _bewijs(stuk.get("bewijs"), bekend)
        if _tekst(stuk.get("bewering")) and not goed:
            klachten.append(f"oorzaak.{s} heeft geen geldig bewijs. Geef record-ID's of zet de bewering op null.")
        elif goed and len({bekend[i] for i in goed}) == 1:
            klachten.append(f"oorzaak.{s} steunt op één Rol. Voeg een record van een andere Rol toe als het debat dat geeft; verlaag anders de zekerheid.")
    if not _lijst(ruw.get("acties")):
        klachten.append("acties is leeg")
    if not _lijst(ruw.get("risicos")):
        klachten.append("risicos is leeg")
    return klachten


def gevuld(ruw, bekend):
    """Hoeveel delen van de oorzaak hebben een bewering met geldig bewijs, plus acties, risico's en voorspelling."""
    verdict, _ = schoon(ruw, bekend, "x")
    return sum(bool(o["bewering"]) for o in verdict["oorzaak"].values()) + ("voorspelling" in verdict) + (len(verdict["acties"]) > 2) + (len(verdict["risicos"]) > 1)


def schoon(ruw, bekend, run_id):
    """Van het antwoord van de Koning naar een geldig verdict.json: onbekende ID's eruit, zekerheid begrensd."""
    oorzaak = ruw.get("oorzaak") if isinstance(ruw.get("oorzaak"), dict) else {}
    slots = {}
    for s in SLOTS:
        stuk = oorzaak.get(s) if isinstance(oorzaak.get(s), dict) else {}
        bewering, ids_ = _tekst(stuk.get("bewering")), _bewijs(stuk.get("bewijs"), bekend)
        if bewering and ids_:  # elk record dat in de bewering staat, hoort ook in het bewijs
            ids_ = list(dict.fromkeys(ids_ + [i for i in bewijs.ids(bewering) if i in bekend]))
        if not bewering or not ids_:  # zonder bewijs is het eerlijk onbekend
            slots[s] = {"bewering": None, "bewijs": []}
            continue
        rollen = {bekend[i] for i in ids_}
        plafond = {1: PLAFOND_EEN_ROL, 2: PLAFOND_TWEE_ROLLEN}.get(len(rollen), 1.0)
        z = stuk.get("zekerheid")
        z = float(z) if isinstance(z, (int, float)) and not isinstance(z, bool) else 0.5
        slots[s] = {"bewering": bewering, "bewijs": ids_, "zekerheid": round(min(max(z, 0.0), plafond), 2)}

    acties = []
    for a in _lijst(ruw.get("acties")):
        if isinstance(a, dict) and _tekst(a.get("actie")):
            ids_ = _bewijs(a.get("bewijs"), bekend)
            acties.append({"actie": _tekst(a["actie"]), "reden": _tekst(a.get("reden")) or "Volgt uit de oorzaak.", **({"bewijs": ids_} if ids_ else {})})
    risicos = [{"risico": _tekst(r["risico"]), "mitigatie": _tekst(r.get("mitigatie")) or "Nog te bepalen door de Council."}
               for r in _lijst(ruw.get("risicos")) if isinstance(r, dict) and _tekst(r.get("risico"))]
    verdict = {
        "run_id": run_id,
        "samenvatting": _tekst(ruw.get("samenvatting")) or "De Council kon geen samenvatting opstellen.",
        "oorzaak": slots,
        "acties": acties or [{"actie": "Geen actie voor de oorzaak vaststaat.", "reden": "De Council kon geen onderbouwd herstelplan opstellen."}],
        "risicos": risicos or [{"risico": "De oorzaak is niet vastgesteld.", "mitigatie": "Eerst meer bewijs verzamelen."}],
    }
    v = ruw.get("voorspelling")
    if isinstance(v, dict) and _tekst(v.get("bewering")):
        try:
            dag = int(v.get("dag"))
        except (TypeError, ValueError):
            dag = 0
        ids_ = _bewijs(v.get("bewijs"), bekend)
        if dag >= 1 and ids_:
            verdict["voorspelling"] = {"dag": dag, "bewering": _tekst(v["bewering"]), "bewijs": ids_}

    geschillen = []
    for g in _lijst(ruw.get("geschillen")):
        if not isinstance(g, dict) or not _tekst(g.get("onderwerp")) or not isinstance(g.get("standpunten"), dict):
            continue
        standpunten = {}
        for rol, s in g["standpunten"].items():
            rol = str(rol).strip().lower().replace(" ", "_")
            if rol in bewijs.PREFIX.values() and _tekst(s):
                standpunten[rol] = _tekst(s)
        if not standpunten:
            continue
        ids_ = _bewijs(g.get("bewijs"), bekend)
        uitkomst = _tekst(g.get("uitkomst"))
        geschillen.append({"onderwerp": _tekst(g["onderwerp"]), "standpunten": standpunten,
                           "uitkomst": uitkomst if uitkomst and uitkomst.lower() != "null" and ids_ else None, "bewijs": ids_})
    return verdict, geschillen


def schemafouten(naam, data):
    schema = json.loads((ROOT / "schema" / naam).read_text(encoding="utf-8"))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in Draft202012Validator(schema).iter_errors(data)]
