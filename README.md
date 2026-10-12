# Council of Achrona: Starter kit

Starter kit voor challenge 113 van Hack The Future 2026, aangeboden door Roboest.

De Desync heeft de herinneringen en systemen van Achrona versnipperd. Jullie bouwen een **Council**: een zwerm van samenwerkende AI-agenten die debatteren, verspreide kennis analyseren en samen tot een **Council Verdict** komen. De lore, de Rolkaarten en de Kennisbronnen staan in het Challenge pack (link bij de briefing), niet in deze repo.

Deze kit geeft jullie binnen het eerste halfuur een draaiende mini-Council: twee Raadsleden (Oracle en Beast Master) debatteren in twee rondes, een Voorzitter vat samen, en de run schrijft een geldig maar nog leeg `verdict.json` en een `trace.json`. Het echte werk is aan jullie.

## Vooraf installeren

- Python 3.12 of hoger
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- git en een GitHub-account
- een code-editor

## Snel starten

1. Klik op **Use this template** en maak een eigen repo aan. Zet die **openbaar**: zo lever je in (zie onderaan).
2. Clone je repo en ga erin staan.
3. Kopieer `.env.example` naar `.env` en vul het in. Op de dag krijg je via de QR-code op je Teamkaartje een kant-en-klare `.env`.
4. Start de Council:
   ```
   uv run council
   ```
5. Controleer de uitvoer:
   ```
   uv run validate
   ```
   Groen = `verdict.json` en `trace.json` volgen het schema en komen uit dezelfde run.

Geen uv? `pip install -e . && python council.py && python validate.py`.

## Hoe de kit werkt

| Bestand | Wat |
|---|---|
| `council.py` | De orchestratie: round-robin debat in vaste volgorde, daarna de Voorzitter. Schrijft `verdict.json` en `trace.json`. |
| `raadsleden/<rol>.py` | Eén bestand per Raadslid: Rol, naam en publiek perspectief. |
| `kennisbronnen/` | Leeg. Hier komen de Kennisbronnen als je zonder MCP werkt. |
| `schema/` | De JSON-schema's van het Council Verdict en de Debat-trace. |
| `voorbeeld/` | Een ingevuld voorbeeld over een verzonnen zaak (een dorpsmolen), om de vorm te zien. Het valideert bewust niet tegen het Achrona-schema. |
| `validate.py` | Toetst je uitvoer aan de schema's. |

**Een Raadslid toevoegen**: kopieer `raadsleden/oracle.py` naar bv. `raadsleden/engineer.py` en pas `ROL`, `NAAM` en `PERSPECTIEF` aan (gebruik de Rolkaart). Het nieuwe bestand doet vanzelf mee; de volgorde in het debat is alfabetisch op bestandsnaam. Voeg in `.env` een regel `MCP_<ROL>=` toe.

**Kennisbronnen**: standaard leest elk Raadslid zijn Kennisbron via de MCP-server van Roboest. Zet het pad van elke Rol in `.env` (`MCP_ORACLE=https://<host>/oracle/mcp`). Laat je het leeg, dan plakt de kit de bestanden uit `kennisbronnen/<rol>/` in de prompt van dat Raadslid (fallback als MCP even niet werkt).

**Modellen**: het kleine model (`MODEL_KLEIN`) voor de Raadsleden, het grote (`MODEL_GROOT`) voor de Voorzitter. Een andere provider kies je alleen via `.env`; het voorbeeldbestand heeft blokken voor het Roboest-endpoint, OpenRouter en Ollama. Eigen modellen mogen, maar support geven we alleen op het Roboest-endpoint.

**Verbruik**: na elke run toont de kit het aantal tokens per model; het staat ook in `trace.json` onder `verbruik`. Je Teamkaartje heeft een limiet, dus hou het in de gaten.

## Spelregels

- Eén Raadslid per Rol. Elk Raadslid leest alleen de Kennisbron van zijn eigen Rol. Dat is na te kijken in de trace.
- Een Voorzitter mag het debat leiden en het Verdict opstellen, maar leest zelf geen Kennisbron.
- Menselijke tussenkomst mag, maar staat in de trace als `spreker: "mens"`.
- `trace.json` is de ongewijzigde uitvoer van de run die het Verdict maakte. Niet achteraf bewerken.

## Wat moet je zelf bouwen?

- Meer Raadsleden (minstens 3 Rollen; meer Rollen = meer kennis).
- Een Voorzitter die het `verdict.json` echt invult: de oorzaak in drie delen (`ingreep`, `mechanisme`, `verloop`), elk met een bewering en record-ID's als bewijs, een geordend herstelplan (`acties`) en minstens één risico. Weet je een deel niet, laat dan `bewering: null`.
- `geschillen` in de trace: waarover waren de Raadsleden het oneens, en hoe werd het beslecht?

## Extra uitdagingen

Zonder Extra uitdaging haal je per criterium hoogstens 8 op 10. Voor wie sneller klaar is:

- **Verdict**: voorspel wanneer de Desync opnieuw toeslaat als niemand ingrijpt (optioneel veld `voorspelling {dag, bewering, bewijs[]}`) · geef je zekerheid per deel van de oorzaak (optioneel veld `zekerheid`, 0–1, per slot).
- **Council**: laat de Voorzitter zelf kiezen wie spreekt, en waarom · laat Raadsleden elkaar gericht bevragen (optioneel veld `aan` per beurt).
- **Presentatie**: draai de Council live tijdens de pitch · visualiseer het debat of geef de Rollen avatars.

## Volgende stappen

- Vervang het vaste round-robin door de group chat-orchestratie of Magentic uit [Microsoft Agent Framework](https://github.com/microsoft/agent-framework).
- Bouw RAG op de Kennisbronnen: het Roboest-endpoint biedt ook een embeddingmodel aan.
- Liever .NET? Zie de MAF .NET-quickstart in dezelfde repo. Liever een ander platform? Mag ook.

## Inleveren (om 16:00)

- Je repo is **openbaar** en je deelt de link met ons via het inleverformulier (link bij de briefing).
- `verdict.json` en `trace.json` staan in de root, uit dezelfde run.
- Alleen de laatste commit vóór 16:00 telt.

## Licentie

MIT, zie `LICENSE`.
