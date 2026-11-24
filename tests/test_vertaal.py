"""De Engelse weergave mag niets aan het bewijs veranderen. Geen key en geen netwerk nodig."""
import vertaal


def test_ids_en_getallen_moeten_blijven():
    nl = "Op dag 3 om 02:40 zakte de put met 0,5 el (ENG-901)."
    assert vertaal.klopt(nl, "On day 3 at 02:40 the well sank 0.5 ells (ENG-901).")
    assert not vertaal.klopt(nl, "On day 3 at 02:40 the well sank 0.5 ells (ENG-902)."), "ander record"
    assert not vertaal.klopt(nl, "On day 4 at 02:40 the well sank 0.5 ells (ENG-901)."), "andere dag"
    assert not vertaal.klopt(nl, "On day 3 the well sank 0.5 ells (ENG-901)."), "tijdstip kwijt"
    assert not vertaal.klopt(nl, "")
    assert vertaal.klopt("Vier nachten lang.", "For 4 nights."), "een getal uitschrijven in cijfers mag"


def test_verzamel_neemt_elke_zichtbare_tekst_een_keer():
    trace = {"beurten": [{"soort": "routering", "vraag": "v", "reden": "r", "hypothese": "h", "open_vragen": ["o", "v"]},
                         {"soort": "toolcall", "tool": "zoek"}, {"soort": "bericht", "tekst": "t"}],
             "geschillen": [{"onderwerp": "g", "standpunten": {"oracle": "s"}, "uitkomst": None}]}
    verdict = {"samenvatting": "sam", "oorzaak": {"ingreep": {"bewering": "b"}, "verloop": {"bewering": None}},
               "acties": [{"actie": "a", "reden": "r"}], "risicos": [{"risico": "x", "mitigatie": "m"}]}
    assert vertaal.verzamel(trace, verdict) == ["v", "r", "h", "o", "t", "g", "s", "sam", "b", "a", "x", "m"]


def test_porties_houden_alles_in_volgorde():
    teksten = [c * 2000 for c in "abcdef"]
    porties = list(vertaal.in_porties(teksten))
    assert [t for p in porties for t in p] == teksten and len(porties) > 1
