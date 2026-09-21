import json
from datetime import datetime, timezone

from backup_lab.runtime import engine


def handler(event, context):
    controller = engine()
    action = event.get("action", "start")
    if action == "start":
        # Daily schedules and their retries share an id. Manual runs supply a fresh id.
        run_id = event.get("run_id") or datetime.now(timezone.utc).strftime("daily-%Y%m%d")
        result = controller.start(run_id)
    elif action == "tick":
        result = controller.tick(event["run_id"])
    elif action == "janitor":
        result = controller.janitor()
    else:
        raise ValueError("Unsupported action")
    print(json.dumps({"component": "backup-under-test", "action": action, **result}))
    return result
