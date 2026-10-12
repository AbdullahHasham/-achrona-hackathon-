"""Toetst verdict.json en trace.json aan de schema's in schema/. Start met `uv run validate`."""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parent


def fouten(naam):
    pad = ROOT / naam
    if not pad.exists():
        return None, [f"{naam} ontbreekt in de root van de repo"]
    data = json.loads(pad.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "schema" / naam).read_text(encoding="utf-8"))
    return data, [f"{naam}: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                  for e in Draft202012Validator(schema).iter_errors(data)]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    verdict, f1 = fouten("verdict.json")
    trace, f2 = fouten("trace.json")
    alle = f1 + f2
    if verdict and trace and verdict.get("run_id") != trace.get("run_id"):
        alle.append("run_id van verdict.json en trace.json verschilt: ze moeten uit dezelfde run komen")
    for f in alle:
        print("FOUT", f)
    print("GROEN: verdict.json en trace.json zijn geldig." if not alle else f"ROOD: {len(alle)} fout(en).")
    sys.exit(1 if alle else 0)


if __name__ == "__main__":
    main()
