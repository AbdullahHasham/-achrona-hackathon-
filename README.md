# Council of Achrona: Starter kit

Starter kit voor challenge 113 van Hack The Future 2026, aangeboden door Roboest.

De Desync heeft de herinneringen en systemen van Achrona versnipperd. Jullie bouwen een **Council**: een zwerm van samenwerkende AI-agenten (Raadsleden) die elk één Rol vertolken, debatteren, verspreide kennis analyseren en samen tot een **Council Verdict** komen: wat is de oorzaak, wat is het herstelplan, welke risico's zijn er.

Deze kit geeft jullie binnen het eerste halfuur een draaiende mini-Council die je live in je browser ziet debatteren. Het echte werk is aan jullie.

## Wat je krijgt op de dag

| Wat | Wanneer | Waar |
|---|---|---|
| Deze repo (GitHub-template) | vooraf | GitHub |
| Teamkaartje: QR-code naar een kant-en-klare `.env` (LLM-toegang tot 18:00) | 9:30 | briefing |
| Challenge pack: Rolkaarten + Kennisbronnen (bestanden) | 10:00 | `https://<mcp-host>/pack.zip` |
| MCP-server: één pad per Rol, `https://<mcp-host>/<rol>/mcp` | 10:00 | in je `.env` |
| Coaches | de hele dag | in de zaal |

## Vooraf installeren

- Python 3.12 of hoger
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- git en een GitHub-account
- een code-editor

## Snel starten

1. Klik op **Use this template** en maak een eigen repo aan. Zet die **openbaar**: zo lever je in (zie onderaan).
2. Clone je repo en ga erin staan.
3. Kopieer `.env.example` naar `.env` en vul het in (of gebruik de `.env` van je Teamkaartje).
4. Open de debatviewer in een tweede terminal:
   ```
   uv run viewer
   ```
5. Start de Council:
   ```
   uv run council
   ```
   In de viewer zie je live wie aan het woord is, wat elk Raadslid zegt, welke Kennisbron het leest en hoeveel tokens het kost.
6. Controleer de uitvoer:
   ```
   uv run validate
   ```
   Groen = `verdict.json` en `trace.json` volgen het schema en komen uit dezelfde run.

Geen uv? `pip install -e . && python council.py && python validate.py`.

## Hoe de kit werkt

```
             ┌────────────── jouw laptop ──────────────┐
 Teamkaartje │  council.py ── Raadslid (Oracle) ───────┼──► MCP  /oracle/mcp
   (.env) ──►│      │      ── Raadslid (Beast Master) ─┼──► MCP  /beast_master/mcp
             │      │      ── Voorzitter (geen bron)   │
             │      ▼                                   │
             │  trace.json + verdict.json ──► viewer    │──► LLM via Roboest-endpoint
             └──────────────────────────────────────────┘
```

| Bestand | Wat |
|---|---|
| `council.py` | De orchestratie: round-robin debat in vaste volgorde, daarna de Voorzitter. Schrijft `trace.json` na elke beurt en `verdict.json` aan het einde. |
| `raadsleden/<rol>.py` | Eén bestand per Raadslid: Rol, naam en publiek perspectief. |
| `viewer.py` + `viewer.html` | De debatviewer (`uv run viewer`). Leest alleen `trace.json` en `verdict.json`, dus werkt ook als je een ander platform kiest. |
| `voorbeelden/devui_debat.py` | Dezelfde Raadsleden als MAF-group chat in de DevUI van Microsoft (zie "Volgende stappen"). |
| `kennisbronnen/` | Leeg. Hier komen de Kennisbronnen uit `pack.zip` als je zonder MCP werkt. |
| `schema/` | De JSON-schema's van het Council Verdict en de Debat-trace. |
| `voorbeeld/` | Een ingevuld voorbeeld over een verzonnen zaak (een dorpsmolen), om de vorm te zien. Het valideert bewust niet tegen het Achrona-schema. |
| `validate.py` | Toetst je uitvoer aan de schema's. |

## Wat stel je zelf in?

- **Welke Rollen** meedoen: kopieer `raadsleden/oracle.py` naar bv. `raadsleden/engineer.py` en pas `ROL`, `NAAM` en `PERSPECTIEF` aan (gebruik de Rolkaart). Het nieuwe bestand doet vanzelf mee; de volgorde in het debat is alfabetisch op bestandsnaam. Voeg in `.env` een regel `MCP_<ROL>=https://<mcp-host>/<rol>/mcp` toe.
- **Kennisbronnen**: standaard leest elk Raadslid via MCP. Laat je `MCP_<ROL>` leeg, dan plakt de kit de bestanden uit `kennisbronnen/<rol>/` (uit `pack.zip`) in de prompt van dat Raadslid.
- **Modellen**: `MODEL_KLEIN` voor de Raadsleden, `MODEL_GROOT` voor de Voorzitter, `REASONING` voor het denkwerk. Een andere provider kies je alleen via `.env` (blokken voor het Roboest-endpoint, OpenRouter en Ollama). Eigen modellen mogen, maar support geven we alleen op het Roboest-endpoint.
- **Rondes**: `RONDES` in `.env`.
- **Het debat zelf**: de instructies, de volgorde, wie wanneer spreekt, en vooral hoe de Voorzitter het Verdict invult. Dat staat allemaal in `council.py`.

**Verbruik**: je Teamkaartje heeft een limiet. De viewer en de terminal tonen het tokenverbruik per run; het staat ook in `trace.json` onder `verbruik`. Bij een rate limit (429) wacht de kit en probeert opnieuw.

## Spelregels

- Eén Raadslid per Rol. Elk Raadslid leest alleen de Kennisbron van zijn eigen Rol. De viewer en de jury kijken dat na in de trace.
- Een Voorzitter mag het debat leiden en het Verdict opstellen, maar leest zelf geen Kennisbron.
- Menselijke tussenkomst mag, maar staat in de trace als `spreker: "mens"`.
- `trace.json` is de ongewijzigde uitvoer van de run die het Verdict maakte. Niet achteraf bewerken.
- Elke Rol heeft een Belang en kan ernaast zitten.

## Wat moet je zelf bouwen?

- Meer Raadsleden (minstens 3 Rollen; meer Rollen = meer kennis). De Kennisbronnen verwijzen soms naar andere Rollen: dat zegt je welke Rol je nog mist.
- Een Voorzitter die het `verdict.json` echt invult: de oorzaak in drie delen (`ingreep`, `mechanisme`, `verloop`), elk met een bewering en record-ID's als bewijs, een geordend herstelplan (`acties`) en minstens één risico. Weet je een deel niet, laat dan `bewering: null`.
- `geschillen` in de trace: waarover waren de Raadsleden het oneens, en hoe werd het beslecht?
- Let op: modellen schrijven ID's soms met een speciaal koppelteken (`ORA‑101`). `uv run validate` meldt dat; vervang het door een gewoon `-`.

## Jurering

Drie criteria, elk 0–10: **Verdict** (50%), **Council** (30%) en **Presentatie** (20%). Zonder Extra uitdaging haal je per criterium hoogstens 8 op 10. Voor wie sneller klaar is:

- **Verdict**: voorspel wanneer de Desync opnieuw toeslaat als niemand ingrijpt (optioneel veld `voorspelling {dag, bewering, bewijs[]}`) · geef je zekerheid per deel van de oorzaak (optioneel veld `zekerheid`, 0–1, per slot).
- **Council**: laat de Voorzitter zelf kiezen wie spreekt, en waarom · laat Raadsleden elkaar gericht bevragen (optioneel veld `aan` per beurt).
- **Presentatie**: draai de Council live tijdens de pitch · bouw een eigen, rijkere visualisatie van het debat (bovenop of in plaats van de viewer) of geef de Rollen avatars.

## Volgende stappen

- **DevUI**: `uv run --extra devui python voorbeelden/devui_debat.py` opent de DevUI van [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) met dezelfde Raadsleden als group chat. Handig om orchestratie te verkennen; het schrijft geen trace.
- Vervang het vaste round-robin door de group chat-manager of Magentic uit MAF, of kies een eigen platform: zolang je `verdict.json` en `trace.json` in dit formaat oplevert, mag alles.
- Bouw RAG op de Kennisbronnen: het Roboest-endpoint biedt ook een embeddingmodel aan.

## Inleveren (om 16:00)

- Je repo is **openbaar** en je deelt de link met ons via het inleverformulier (link bij de briefing).
- `verdict.json` en `trace.json` staan in de root, uit dezelfde run.
- Alleen de laatste commit vóór 16:00 telt.

## Licentie

MIT, zie `LICENSE`.
