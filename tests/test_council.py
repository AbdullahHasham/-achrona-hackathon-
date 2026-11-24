"""Een volledige run zonder key en zonder echte Kennisbronnen.

Het model is nep (een lokale HTTP-server), de MCP-servers zijn echt maar geven verzonnen records over een
waterput. De nepmodellen doen expres fout wat echte modellen fout doen.
"""
import asyncio
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

import bewijs
import council
import koning
import validate

RECORDS = {
    "engineer": "id,dag,wat\nENG-901,3,de put zakt\nENG-902,6,de put zakt weer. Zie HIS-903.",
    "historian": "HIS-901 dag 1: oude putregel.\nHIS-903 dag 2: de smid boorde een gat.",
    "oracle": "ORA-901 dag 9: droom over droogte.",
}


def vrije_poort():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def maak_lezer(tekst):
    def lees_kennisbron() -> str:  # '-> str' geeft structured content, net als de echte server
        return tekst
    return lees_kennisbron


@pytest.fixture(scope="module")
def mcp_servers():
    urls = {}
    for rol, tekst in RECORDS.items():
        poort = vrije_poort()
        app = FastMCP(f"test-{rol}", host="127.0.0.1", port=poort, streamable_http_path=f"/{rol}/mcp", log_level="ERROR")
        app.tool()(maak_lezer(tekst))
        threading.Thread(target=app.run, kwargs={"transport": "streamable-http"}, daemon=True).start()
        urls[rol] = f"http://127.0.0.1:{poort}/{rol}/mcp"
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", poort)) == 0:
                    break
            time.sleep(0.05)
    return urls


class NepModel:
    """Speelt beide modellen. `keuzes` is wat de Koning na n Raadslid-beurten antwoordt."""

    def __init__(self, keuzes, verdict, gaten=None):
        self.gaten = gaten or []
        self.keuzes, self.verdict, self.gezien, self.kuren = keuzes, verdict, [], {}
        nep = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                status, msg = nep.antwoord(body)
                data = json.dumps({
                    "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                    "choices": [{"index": 0, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop", "message": msg}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                } if status == 200 else {"error": {"message": "stuk"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    @staticmethod
    def plat(inhoud):
        return inhoud if isinstance(inhoud, str) else " ".join(d.get("text", "") for d in inhoud or [])

    def antwoord(self, body):
        berichten = [(m["role"], self.plat(m.get("content"))) for m in body["messages"]]
        systeem = " ".join(t for r, t in berichten if r == "system")
        laatste_rol, laatste = berichten[-1]
        self.gezien.append((body, systeem, laatste))
        zeg = lambda t: (200, {"role": "assistant", "content": t})
        if "Je bent de Koning" in systeem:
            assert not body.get("tools"), "de Koning heeft geen tools"
            if "Antwoord met alleen dit JSON-object" in laatste and "Schrijf nu het Council Verdict" in laatste:
                return zeg(self.verdict)
            if "Jij bent de Uitdager" in laatste:
                return zeg(json.dumps({"gaten": self.gaten.pop(0) if self.gaten else []}))
            if "Het debat is gesloten" in laatste:
                return zeg("Oordeel: het gat in de put (HIS‑903).")
            n = int(re.search(r"gebruikt: (\d+) van", laatste)[1])
            keuze = self.na_uitdaging if "De Uitdager noemt deze gaten" in laatste else self.keuzes[min(n, len(self.keuzes) - 1)]
            return zeg(keuze if isinstance(keuze, str) else "Hier is mijn keuze:\n```json\n" + json.dumps(keuze) + "\n```")
        rol = next(r for r in RECORDS if f"Je bent de {r.title()}" in systeem)
        if laatste_rol != "tool":
            return 200, {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{len(self.gezien)}", "type": "function", "function": {"name": "lees_kennisbron", "arguments": "{}"}}]}
        kuur = self.kuren.pop(rol, None)
        if kuur:
            return zeg(kuur)
        return zeg(f"Mijn bron zegt: {', '.join(bewijs.ids(laatste))}.")


def keuze(rol, vraag="Wat staat er in je bron?", reden="ENG-902 wijst naar de Historian.", stand="vermoeden", **extra):
    return {"stand": {s: stand for s in koning.SLOTS}, "open_vragen": ["Wie boorde?"], "toetst": [], "rol": rol,
            "vraag": vraag if rol else None, "reden": reden, **extra}


GOED_VERDICT = json.dumps({
    "samenvatting": "De smid boorde een gat.",
    "oorzaak": {
        "ingreep": {"bewering": "Dag 2: de smid boorde.", "bewijs": ["HIS‑903", "ENG-902"], "zekerheid": 0.95},
        "mechanisme": {"bewering": "Het water loopt weg.", "bewijs": ["ENG-901"], "zekerheid": 0.9},
        "verloop": {"bewering": "Om de drie dagen.", "bewijs": ["BEA-777"], "zekerheid": 0.8},
    },
    "acties": [{"actie": "Gat dichten", "reden": "Dan stopt het.", "bewijs": ["HIS-903", "GUA-555"]}],
    "risicos": [{"risico": "Te snel vullen", "mitigatie": "Traag vullen"}],
    "voorspelling": {"dag": "9", "bewering": "3, 6, dus 9.", "bewijs": ["ENG-901", "ENG-902"]},
    "geschillen": [{"onderwerp": "Gat of droogte?", "standpunten": {"Oracle": "droogte", "engineer": "gat", "smid": "x"},
                    "uitkomst": "Gat.", "bewijs": ["HIS-903"]}],
})


def draai(monkeypatch, tmp_path, mcp_servers, nep, **env):
    for k, v in {"OPENAI_BASE_URL": nep.url, "OPENAI_API_KEY": "test", "MODEL_KLEIN": "klein", "MODEL_GROOT": "groot",
                 "COUNCIL_UIT": str(tmp_path / "uit"), "REASONING": "", "ZICHT": "blind", **env}.items():
        monkeypatch.setenv(k, v)
    leden = []
    for rol in RECORDS:
        monkeypatch.setenv(f"MCP_{rol.upper()}", mcp_servers[rol])
        leden.append(SimpleNamespace(ROL=rol, NAAM=rol.replace("_", " ").title(), PERSPECTIEF="Kijkt.", BELANG="rust"))
    monkeypatch.setattr(council.raadsleden, "alle", lambda: leden)
    monkeypatch.setattr(council, "ROOT", tmp_path)
    asyncio.run(council.run())
    lees = lambda n: json.loads((tmp_path / "uit" / n).read_text(encoding="utf-8"))
    trace, verdict = lees("trace.json"), lees("verdict.json")
    assert koning.schemafouten("trace.json", trace) == []
    assert koning.schemafouten("verdict.json", verdict) == []
    assert validate.spelregels(trace) == []
    assert trace["run_id"] == verdict["run_id"]
    assert len(list((tmp_path / "runs").iterdir())) == 1, "elke run wordt bewaard"
    return trace, verdict


def test_volledige_run(monkeypatch, tmp_path, mcp_servers):
    nep = NepModel([keuze("engineer"), keuze("Historian"), keuze("oracle"), keuze(None, reden="Alles is getoetst.", stand="getoetst")], GOED_VERDICT)
    nep.kuren["historian"] = "Ik verzin HIS‑999."
    trace, verdict = draai(monkeypatch, tmp_path, mcp_servers, nep)
    b = trace["beurten"]

    routes = [x for x in b if x["soort"] == "routering"]
    assert [r["aan"] for r in routes] == ["engineer", "historian", "oracle", None]
    assert routes[-1]["reden"] == "Alles is getoetst." and routes[-1]["open_vragen"] == ["Wie boorde?"]
    assert all(r["spreker"] == "koning" and r["stand"] for r in routes)

    for rol in RECORDS:
        eerste = next(x for x in b if x["spreker"] == rol)
        assert eerste["soort"] == "toolcall" and f"/{rol}/mcp" in eerste["pad"], "eerst lezen, dan spreken"

    per = {x["spreker"]: x for x in b if x["soort"] == "bericht" and x["spreker"] != "koning"}
    assert per["engineer"]["id_controle"]["eigen"] == ["ENG-901", "ENG-902"], "een tabelrij op een nieuwe regel telt als gelezen"
    assert "herkansing" not in per["engineer"]
    assert per["engineer"]["id_controle"]["verwijzingen"] == ["HIS-903"]
    assert "HIS-999" in per["historian"]["herkansing"], "een verzonnen ID wordt afgekeurd"
    assert "HIS-999" not in per["historian"]["tekst"]

    o = verdict["oorzaak"]
    assert o["ingreep"]["bewijs"] == ["HIS-903", "ENG-902"], "koppelteken hersteld"
    assert o["ingreep"]["zekerheid"] == koning.PLAFOND_TWEE_ROLLEN
    assert o["mechanisme"]["zekerheid"] == koning.PLAFOND_EEN_ROL
    assert o["verloop"] == {"bewering": None, "bewijs": []}, "bewijs dat niemand inbracht telt niet"
    assert verdict["acties"][0]["bewijs"] == ["HIS-903"]
    assert verdict["voorspelling"]["dag"] == 9
    assert trace["geschillen"][0]["standpunten"] == {"oracle": "droogte", "engineer": "gat"}


def test_eerste_getuigenis_is_blind_daarna_niet(monkeypatch, tmp_path, mcp_servers):
    nep = NepModel([keuze("engineer"), keuze("historian"), keuze("engineer"), keuze(None)], GOED_VERDICT)
    trace, _ = draai(monkeypatch, tmp_path, mcp_servers, nep)
    vragen = [l for _, s, l in nep.gezien if "Raadslid in de Council" in s and "Vraag van de Koning" in l]
    assert "niet wat de anderen zeiden" in vragen[1] and "ENG-901" not in vragen[1]
    assert "[Historian]" in vragen[2] and "HIS-903" in vragen[2]
    assert [x["zicht"] for x in trace["beurten"] if x["soort"] == "bericht" and x["spreker"] != "koning"] == ["vraag", "vraag", "debat"]


def test_beurtenbudget_geldt_altijd(monkeypatch, tmp_path, mcp_servers):
    rollen = ["engineer", "historian", "oracle"]
    nep = NepModel([keuze(rollen[n % 3]) for n in range(20)], GOED_VERDICT)
    trace, _ = draai(monkeypatch, tmp_path, mcp_servers, nep)
    assert len(bewijs.berichten(trace["beurten"])) == council.BEURTENBUDGET
    assert trace["beurten"][-2]["aan"] is None


def test_een_koning_die_wartaal_spreekt_breekt_de_run_niet(monkeypatch, tmp_path, mcp_servers):
    nep = NepModel(["geen json hier"], "ook geen json")
    trace, verdict = draai(monkeypatch, tmp_path, mcp_servers, nep)
    routes = [x for x in trace["beurten"] if x["soort"] == "routering"]
    assert all("Noodregel" in r["reden"] for r in routes)
    assert {r["aan"] for r in routes} == {*RECORDS, None}
    assert routes[1]["aan"] == "historian", "de noodregel volgt eerst het open spoor"
    assert all(verdict["oorzaak"][s]["bewering"] is None for s in koning.SLOTS), "liever onbekend dan verzonnen"


def test_de_uitdager_kan_het_debat_openhouden(monkeypatch, tmp_path, mcp_servers):
    gat = {"gat": "Niemand vroeg de Oracle naar dag 9.", "rol": "Oracle", "vraag": "Wat zag je op dag 9?"}
    stopt = keuze(None, reden="Genoeg.")
    nep = NepModel([keuze("engineer"), keuze("historian"), stopt, stopt], GOED_VERDICT, gaten=[[gat]])
    nep.na_uitdaging = keuze("oracle", reden="De Uitdager wijst op dag 9.")
    trace, _ = draai(monkeypatch, tmp_path, mcp_servers, nep)
    routes = [x for x in trace["beurten"] if x["soort"] == "routering"]
    assert [r["aan"] for r in routes] == ["engineer", "historian", "oracle", None]
    assert routes[2]["uitdaging"] == ["Niemand vroeg de Oracle naar dag 9."], "het gat staat in de trace"
    assert "uitdaging" not in routes[3], "de Uitdager komt één keer per run"


def test_de_koning_mag_na_een_uitdaging_toch_sluiten(monkeypatch, tmp_path, mcp_servers):
    gat = {"gat": "Iets kleins.", "rol": "oracle", "vraag": "?"}
    nep = NepModel([keuze("engineer"), keuze(None, reden="Genoeg.")], GOED_VERDICT, gaten=[[gat]])
    nep.na_uitdaging = keuze(None, reden="Dat gat verandert het Verdict niet.")
    trace, _ = draai(monkeypatch, tmp_path, mcp_servers, nep)
    laatste = [x for x in trace["beurten"] if x["soort"] == "routering"][-1]
    assert laatste["aan"] is None and laatste["reden"] == "Dat gat verandert het Verdict niet." and laatste["uitdaging"] == ["Iets kleins."]


def test_zoektermen_en_bord_gaan_mee(monkeypatch, tmp_path, mcp_servers):
    bord = [{"naam": "Gat", "voor": ["ENG\u2011901: zakt"], "tegen": [], "stand": "leidend"}, {"naam": "Droogte", "voor": [], "tegen": ["x"], "stand": "onzin"}, "kapot"]
    nep = NepModel([keuze("engineer"), keuze("historian", zoektermen=["smid", "dag 2"], verklaringen=bord), keuze(None)], GOED_VERDICT)
    trace, _ = draai(monkeypatch, tmp_path, mcp_servers, nep)
    r = [x for x in trace["beurten"] if x["soort"] == "routering"][1]
    assert r["zoektermen"] == ["smid", "dag 2"]
    assert r["verklaringen"] == [{"naam": "Gat", "voor": ["ENG-901: zakt"], "tegen": [], "stand": "leidend"},
                                 {"naam": "Droogte", "voor": [], "tegen": ["x"], "stand": "open"}]
    vraag = next(l for _, sys_, l in nep.gezien if "Je bent de Historian" in sys_ and "Vraag van de Koning" in l)
    assert "smid; dag 2" in vraag and "ENG-901" not in vraag, "wel zoektermen, niet het debat"
    koning_ziet = [l for _, sys_, l in nep.gezien if "Je bent de Koning" in sys_ and "Verklaring 'Gat' (leidend)" in l]
    assert koning_ziet, "de Koning ziet zijn eigen bord terug"


def test_id_in_de_bewering_hoort_in_het_bewijs():
    ruw = {"oorzaak": {"ingreep": {"bewering": "De smid (HIS-903) na ENG-901 en BEA-777.", "bewijs": ["HIS-903"], "zekerheid": 0.5}}}
    v, _ = koning.schoon(ruw, {"HIS-903": "historian", "ENG-901": "engineer"}, "x")
    assert v["oorzaak"]["ingreep"]["bewijs"] == ["HIS-903", "ENG-901"]


def test_debat_in_het_engels(monkeypatch, tmp_path, mcp_servers):
    nep = NepModel([keuze("engineer"), keuze(None)], GOED_VERDICT)
    trace, _ = draai(monkeypatch, tmp_path, mcp_servers, nep, COUNCIL_TAAL="en")
    assert trace["taal"] == "en"
    assert all("Engels" in s for _, s, _ in nep.gezien), "Koning en Raadsleden weten het allebei"


def test_json_uit_tekst():
    assert koning.json_uit('tekst {"a": {"b": 1}} na') == {"a": {"b": 1}}
    assert koning.json_uit('{kapot} en dan {"a": 1}') == {"a": 1}
    with pytest.raises(ValueError):
        koning.json_uit("niets")


def test_id_controle():
    c = bewijs.controleer("oracle", "ORA–971, ORA-972, HIS-971 en ENG-972", {"ORA-971", "HIS-971"})
    assert c == {"eigen": ["ORA-971"], "niet_in_bron": ["ORA-972"], "verwijzingen": ["HIS-971"], "van_anderen": ["ENG-972"]}
    assert bewijs.normaliseer("ORA‑971") == "ORA-971"
