"""Validate the rendered YaWL against Yandex's schema and check all step references."""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_URL = "https://raw.githubusercontent.com/yandex-cloud/json-schema-store/master/serverless/workflows/yawl.json"


def validate_flow(flow):
    """Explicit graph cycles are forbidden, including within a While's own scope."""
    steps = flow["steps"]
    if flow["start"] not in steps:
        raise ValueError("Unknown start step")

    def references(value):
        if isinstance(value, dict):
            if "start" in value and "steps" in value:
                validate_flow(value)
                return
            for key, child in value.items():
                if key == "next":
                    if child not in steps:
                        raise ValueError(f"Unknown step in this scope: {child}")
                    yield child
                else:
                    yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    edges = {name: list(references(step)) for name, step in steps.items()}
    visiting, done = set(), set()

    def visit(name):
        if name in visiting:
            raise ValueError(f"Cycle at step {name}; use While")
        if name in done:
            return
        visiting.add(name)
        for target in edges[name]:
            visit(target)
        visiting.remove(name)
        done.add(name)

    for name in steps:
        visit(name)


def main():
    cache = ROOT / "build" / "yawl-schema.json"
    cache.parent.mkdir(exist_ok=True)
    if not cache.exists():
        with urlopen(SCHEMA_URL, timeout=20) as response:
            cache.write_bytes(response.read())
    schema = json.loads(cache.read_text(encoding="utf-8"))
    spec = yaml.safe_load(
        (ROOT / "workflows/drill.yaml.tftpl").read_text().replace("${function_id}", "test-function")
    )
    jsonschema.Draft4Validator(schema).validate(spec)
    validate_flow(spec)
    print("YaWL schema, scoped step references and acyclic graphs: PASS")
    print("Schema SHA-256:", hashlib.sha256(cache.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
