"""Self-hosted multi-tenant Streamable HTTP MCP server for the Council of Achrona.

Serves six strictly isolated MCP endpoints on a single local ASGI server:
  - http://127.0.0.1:8200/archmage/mcp
  - http://127.0.0.1:8200/beast_master/mcp
  - http://127.0.0.1:8200/engineer/mcp
  - http://127.0.0.1:8200/guardian_keeper/mcp
  - http://127.0.0.1:8200/historian/mcp
  - http://127.0.0.1:8200/oracle/mcp

Each endpoint exposes `lees_kennisbron`, `zoek`, and `toon` scoped strictly to
its own role directory (enforcing hard departmental data isolation). By default,
it serves `demo_scenario/<role>/` (or `kennisbronnen/<role>/` if present and
selected via `--data-dir` / `MCP_DATA_DIR`).

Usage:
    python mcp_server.py
    python mcp_server.py --port 8200 --data-dir demo_scenario
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

ROOT = Path(__file__).parent
ROLES = (
    "archmage",
    "beast_master",
    "engineer",
    "guardian_keeper",
    "historian",
    "oracle",
)


def _load_role_corpus(data_dir: Path, role: str) -> str:
    """Read all `.md` and `.csv` files for a single isolated council role."""
    role_dir = data_dir / role
    if not role_dir.is_dir():
        return f"(No knowledge-source files found in {role_dir})"
    files = sorted(p for p in role_dir.glob("*") if p.suffix in (".md", ".csv"))
    if not files:
        return f"(No .md or .csv files found in {role_dir})"
    parts = [f"## {p.name}\n\n{p.read_text(encoding='utf-8').strip()}" for p in files]
    return "\n\n".join(parts)


def _build_role_mcp(data_dir: Path, role: str) -> FastMCP:
    """Create an isolated FastMCP instance bound exclusively to one role."""
    mcp = FastMCP(
        name=f"council-{role}",
        stateless_http=True,
        streamable_http_path="/",
    )

    @mcp.tool()
    def lees_kennisbron() -> str:
        """Return the complete private knowledge source for this council role."""
        return _load_role_corpus(data_dir, role)

    @mcp.tool()
    def zoek(term: str) -> str:
        """Search lines in this role's private knowledge source matching `term`."""
        corpus = _load_role_corpus(data_dir, role)
        needle = term.strip().lower()
        if not needle:
            return "Provide a non-empty search term."
        matches = [line for line in corpus.splitlines() if needle in line.lower()]
        return "\n".join(matches) if matches else f"No matching lines for '{term}'."

    @mcp.tool()
    def toon(ids: str) -> str:
        """Return lines matching one or more comma-separated record IDs."""
        corpus = _load_role_corpus(data_dir, role)
        wanted = {tok.strip().upper() for tok in re.split(r"[,\s]+", ids) if tok.strip()}
        if not wanted:
            return "Provide at least one record ID."
        matches = [
            line for line in corpus.splitlines() if any(rid in line.upper() for rid in wanted)
        ]
        return "\n".join(matches) if matches else f"No records found for: {', '.join(sorted(wanted))}."

    return mcp


def create_app(data_dir: Path) -> Starlette:
    """Mount all six isolated role MCP servers under `/<role>/mcp`."""
    role_servers = {role: _build_role_mcp(data_dir, role) for role in ROLES}

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        async with contextlib.AsyncExitStack() as stack:
            for server in role_servers.values():
                await stack.enter_async_context(server.session_manager.run())
            yield

    async def health(_request):
        return JSONResponse(
            {
                "status": "ok",
                "data_dir": str(data_dir),
                "endpoints": [f"/{role}/mcp" for role in ROLES],
            }
        )

    routes: list[Route | Mount] = [Route("/health", health)]
    for role, server in role_servers.items():
        routes.append(Mount(f"/{role}/mcp", app=server.streamable_http_app()))

    return Starlette(routes=routes, lifespan=lifespan)


def resolve_data_dir(explicit: str | None = None) -> Path:
    """Choose explicit data dir, `MCP_DATA_DIR`, or default to `demo_scenario`."""
    chosen = explicit or os.getenv("MCP_DATA_DIR")
    if chosen:
        path = Path(chosen)
        return path if path.is_absolute() else (ROOT / path)
    return ROOT / "demo_scenario"


def main() -> None:
    parser = argparse.ArgumentParser(description="Local multi-tenant MCP server for Council of Achrona.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8200")), help="Bind port (default: 8200)")
    parser.add_argument("--data-dir", default=None, help="Directory containing <role>/ folders (default: demo_scenario)")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data_dir)
    print(f"Starting Council Multi-Tenant MCP Server on http://{args.host}:{args.port}")
    print(f"Serving isolated role folders from: {data_dir}")
    for role in ROLES:
        print(f"  - {role:<16} -> http://{args.host}:{args.port}/{role}/mcp")

    app = create_app(data_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
