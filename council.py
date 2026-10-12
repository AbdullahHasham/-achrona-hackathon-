"""Council of Achrona: een mini-Council met round-robin debat en een Voorzitter.

Start met `uv run council`. Schrijft verdict.json en trace.json in de root van de repo.
"""
import asyncio
import json
import os
import sys
import uuid
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv
from openai import AsyncOpenAI

import raadsleden

ROOT = Path(__file__).parent
KENNISBRONNEN = ROOT / "kennisbronnen"


def client(model_env):
    key = os.environ["OPENAI_API_KEY"]
    return OpenAIChatCompletionClient(
        model=os.environ[model_env],
        async_client=AsyncOpenAI(
            base_url=os.environ["OPENAI_BASE_URL"],
            api_key=key,
            default_headers={"api-key": key},  # het Roboest-endpoint leest de key uit deze header
            max_retries=8,  # bij een rate limit (429) wacht de client en probeert opnieuw
        ),
    )


def kennisbron_bestanden(rol):
    map_ = KENNISBRONNEN / rol
    return sorted(p for p in map_.glob("*") if p.suffix in (".md", ".csv")) if map_.is_dir() else []


def instructies(lid, bestanden, mcp):
    tekst = (
        f"Je bent de {lid.NAAM}, een Raadslid in de Council of Achrona. {lid.PERSPECTIEF}\n"
        "Je debatteert met de andere Raadsleden over de oorzaak van de Desync. "
        "Baseer je alleen op je eigen Kennisbron en noem bij elke bewering de record-ID's precies zoals ze in je Kennisbron staan. Verzin nooit een ID. "
        "Zeg het eerlijk als je iets niet weet."
    )
    if mcp:
        tekst += "\nLees of doorzoek je Kennisbron met je tools voor je spreekt."
    elif bestanden:
        tekst += "\n\nJe Kennisbron:\n\n" + "\n\n".join(f"## {p.name}\n\n{p.read_text(encoding='utf-8')}" for p in bestanden)
    else:
        tekst += "\nJe hebt nog geen Kennisbron: zeg dat je niets kan bewijzen."
    return tekst


def tel(verbruik, model, usage):
    if not usage:
        return
    v = verbruik.setdefault(model, {"calls": 0, "invoer": 0, "gecached": 0, "uitvoer": 0})
    v["calls"] += 1
    v["invoer"] += usage.get("input_token_count") or 0
    v["gecached"] += usage.get("cache_read_input_token_count") or 0
    v["uitvoer"] += usage.get("output_token_count") or 0


def toolcalls(response):
    for msg in response.messages:
        for c in msg.contents:
            if c.type == "function_call":
                args = c.arguments
                yield c.name, json.loads(args) if isinstance(args, str) and args else (args or {})


async def run():
    load_dotenv(ROOT / ".env")
    rondes = int(os.getenv("RONDES", "2"))
    opties = {"reasoning_effort": os.environ["REASONING"]} if os.getenv("REASONING") else None  # minder denkwerk = minder tokens
    run_id = f"{datetime.now().astimezone().isoformat(timespec='seconds')}_{uuid.uuid4().hex[:8]}"
    leden = raadsleden.alle()
    beurten, verbruik = [], {}
    trace = {"run_id": run_id, "status": "bezig", "raadsleden": [l.ROL for l in leden], "beurten": beurten, "geschillen": [], "verbruik": verbruik}

    def bewaar(naam, data):
        (ROOT / naam).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def beurt(ronde, spreker, soort, **velden):
        beurten.append({"nr": len(beurten) + 1, "ronde": ronde, "spreker": spreker, "soort": soort, **velden})
        bewaar("trace.json", trace)  # na elke beurt: de viewer (`uv run viewer`) toont het debat live

    (ROOT / "verdict.json").unlink(missing_ok=True)  # geen Verdict van een vorige run laten staan
    klein = client("MODEL_KLEIN")

    async with AsyncExitStack() as stack:
        agents = []
        for lid in leden:
            mcp_url = os.getenv(f"MCP_{lid.ROL.upper()}", "").strip()
            bestanden = [] if mcp_url else kennisbron_bestanden(lid.ROL)
            tools = []
            if mcp_url:
                tools.append(await stack.enter_async_context(MCPStreamableHTTPTool(name=f"kennisbron_{lid.ROL}", url=mcp_url)))
            for p in bestanden:  # fallback zonder MCP: welke bestanden gingen in de prompt
                beurt(0, lid.ROL, "bestand", bestand=f"{lid.ROL}/{p.name}")
            agents.append((lid, mcp_url, Agent(klein, instructies(lid, bestanden, mcp_url), name=lid.ROL, tools=tools)))

        debat = []
        for ronde in range(1, rondes + 1):
            for lid, mcp_url, agent in agents:
                vraag = (
                    "Debat tot nu toe:\n\n" + ("\n\n".join(debat) or "(nog niets)")
                    + f"\n\nRonde {ronde} van {rondes}. Geef je inbreng in hoogstens 150 woorden. "
                    "Reageer op wat de anderen zeiden."
                )
                trace["aan_het_woord"] = lid.ROL
                bewaar("trace.json", trace)
                r = await agent.run(vraag, options=opties)
                tel(verbruik, "klein", r.usage_details)
                for tool, args in toolcalls(r):
                    beurt(ronde, lid.ROL, "toolcall", tool=tool, argumenten=args, pad=mcp_url)
                beurt(ronde, lid.ROL, "bericht", tekst=r.text)
                debat.append(f"{lid.NAAM}: {r.text}")
                print(f"\n[ronde {ronde}] {lid.NAAM}:\n{r.text}")

    # De Voorzitter heeft geen Rol en geen Kennisbron: hij kent alleen wat de Raadsleden inbrachten.
    trace["aan_het_woord"] = "voorzitter"
    bewaar("trace.json", trace)
    voorzitter = Agent(client("MODEL_GROOT"), "Je bent de Voorzitter van de Council of Achrona. Je hebt geen Kennisbron.", name="voorzitter")
    r = await voorzitter.run(
        options=opties,
        messages="Debat:\n\n" + "\n\n".join(debat)
        + "\n\nVat samen: waarover zijn de Raadsleden het eens, waarover niet, en welk bewijs (record-ID's) noemden ze?",
    )
    tel(verbruik, "groot", r.usage_details)
    beurt(rondes + 1, "voorzitter", "bericht", tekst=r.text)
    print(f"\n[Voorzitter]:\n{r.text}")

    # TODO voor jullie team: laat de Council dit Verdict zelf invullen (zie schema/verdict.json).
    verdict = {
        "run_id": run_id,
        "samenvatting": "TODO",
        "oorzaak": {slot: {"bewering": None, "bewijs": []} for slot in ("ingreep", "mechanisme", "verloop")},
        "acties": [{"actie": "TODO", "reden": "TODO"}],
        "risicos": [{"risico": "TODO", "mitigatie": "TODO"}],
    }
    trace["status"], trace["aan_het_woord"] = "klaar", None
    bewaar("trace.json", trace)
    bewaar("verdict.json", verdict)

    print(f"\nKlaar: verdict.json en trace.json geschreven (run_id {run_id}).")
    for model, v in verbruik.items():
        print(f"Verbruik {model}: {v['calls']} calls, {v['invoer']} tokens in ({v['gecached']} gecached), {v['uitvoer']} tokens uit")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run())


if __name__ == "__main__":
    main()
