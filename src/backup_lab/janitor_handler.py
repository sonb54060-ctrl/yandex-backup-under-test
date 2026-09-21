import json

from backup_lab.runtime import engine


def handler(event, context):
    result = engine().janitor()
    print(json.dumps({"component": "backup-under-test", "action": "janitor", **result}))
    return result
