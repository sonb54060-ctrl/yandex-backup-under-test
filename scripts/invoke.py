"""Invoke private functions through the operator's yc profile, without passing secrets."""

import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("function", choices=["setup", "controller", "janitor"])
parser.add_argument(
    "action",
    choices=[
        "seed",
        "checkpoint",
        "overwrite",
        "delete-marker",
        "remove-version",
        "mutate-order",
        "start",
        "tick",
        "janitor",
    ],
)
parser.add_argument("--confirm")
parser.add_argument("--backup-id")
parser.add_argument("--run-id")
parser.add_argument("--terraform", default="terraform")
parser.add_argument("--yc", default="yc")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
ids = json.loads(
    subprocess.check_output(
        [args.terraform, "-chdir=infra/automation", "output", "-json", "function_ids"], cwd=root
    )
)
event = {
    k: v
    for k, v in {
        "action": args.action,
        "confirm": args.confirm,
        "backup_id": args.backup_id,
        "run_id": args.run_id,
    }.items()
    if v is not None
}
subprocess.run(
    [args.yc, "serverless", "function", "invoke", ids[args.function], "--data", json.dumps(event)],
    cwd=root,
    check=True,
)
