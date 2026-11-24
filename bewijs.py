"""Bewijsregels in code: welke record-ID's zijn echt gelezen, en door wie ingebracht?

Deze module kent geen inhoud van de Kennisbronnen. Ze vergelijkt alleen de ID's in een bericht met de
ID's die in de toolresultaten van datzelfde Raadslid stonden. De Koning krijgt enkel die ID's te zien.
"""
import re

PREFIX = {
    "ARC": "archmage", "ENG": "engineer", "GUA": "guardian_keeper",
    "HIS": "historian", "ORA": "oracle", "BEA": "beast_master",
}
ID_RE = re.compile(r"\b(" + "|".join(PREFIX) + r")[-‐-―−]([0-9]{3})\b")


def normaliseer(tekst):
    """Modellen schrijven ID's soms met een speciaal koppelteken: maak er een gewoon '-' van."""
    return ID_RE.sub(r"\1-\2", tekst or "")


def ids(tekst):
    """Alle record-ID's in een tekst, elk één keer, in volgorde van voorkomen."""
    return list(dict.fromkeys(f"{m[1]}-{m[2]}" for m in ID_RE.finditer(tekst or "")))


def eigenaar(id_):
    return PREFIX[id_[:3]]


def controleer(rol, tekst, gezien):
    """Vergelijk de ID's in een bericht met de ID's die dit Raadslid in zijn eigen bron zag.

    eigen          eigen records die het echt las
    verwijzingen   records van een andere Rol waar de eigen bron naar verwijst: een spoor
    van_anderen    records die het uit het debat overnam
    niet_in_bron   'eigen' ID's die niet in de toolresultaten stonden: verzonnen of verkeerd geschreven
    """
    uit = {"eigen": [], "verwijzingen": [], "van_anderen": [], "niet_in_bron": []}
    for i in ids(tekst):
        if eigenaar(i) == rol:
            uit["eigen" if i in gezien else "niet_in_bron"].append(i)
        else:
            uit["verwijzingen" if i in gezien else "van_anderen"].append(i)
    return uit


def berichten(beurten):
    return [b for b in beurten if b["soort"] == "bericht" and b["spreker"] in PREFIX.values()]


def ingebracht(beurten):
    """Records die hun eigen Rol las én in het debat bracht. Alleen deze mogen bewijs zijn in het Verdict."""
    return {i: b["spreker"] for b in berichten(beurten) for i in b.get("id_controle", {}).get("eigen", [])}


def opbrengst(beurten):
    """Per Raadslid-beurt: wie sprak, en hoeveel eigen records waren nieuw in het debat?"""
    gezien, uit = set(), []
    for b in berichten(beurten):
        eigen = set(b.get("id_controle", {}).get("eigen", []))
        uit.append((b["spreker"], len(eigen - gezien)))
        gezien |= eigen
    return uit


def register(beurten):
    """Elk ingebracht record met de zin waarin zijn eigen Rol het noemde. Alleen tekst uit het debat."""
    bekend, zinnen = ingebracht(beurten), {}
    for b in berichten(beurten):
        for regel in b["tekst"].splitlines():
            for zin in re.split(r"(?<=[.!?;])\s+", regel):
                for i in ids(zin):
                    if bekend.get(i) == b["spreker"]:
                        zinnen.setdefault(i, zin.strip(" -*#"))
    return {i: zinnen.get(i, "") for i in sorted(bekend)}


def open_sporen(beurten):
    """Records waar een bron naar verwijst, maar die hun eigen Rol nog niet zelf inbracht."""
    bekend, sporen = ingebracht(beurten), {}
    for b in berichten(beurten):
        for i in b.get("id_controle", {}).get("verwijzingen", []):
            if i not in bekend:
                sporen.setdefault(i, []).append(b["spreker"])
    return sporen


def twijfelachtig(beurten):
    bekend = ingebracht(beurten)
    return {i: b["spreker"] for b in berichten(beurten)
            for i in b.get("id_controle", {}).get("niet_in_bron", []) if i not in bekend}
