"""Export only the non-secret base outputs; no shell quoting or JSON encoding ambiguity."""

import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--terraform", default="terraform")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
build = root / "build"
build.mkdir(exist_ok=True)
for output, filename in (("runtime_config", "config.json"), ("automation", "automation.json")):
    raw = subprocess.check_output(
        [args.terraform, "-chdir=infra/base", "output", "-json", output], cwd=root
    )
    value = json.loads(raw)
    (build / filename).write_text(json.dumps(value, indent=2), encoding="utf-8")
    print(filename)
