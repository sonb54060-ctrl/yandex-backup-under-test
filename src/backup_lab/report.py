import html
from datetime import datetime, timezone


def render(report):
    """Offline HTML, with no scripts, external assets, credentials or attachment content."""

    def esc(value):
        return html.escape(str(value), quote=True)

    rows = "".join(
        f"<tr><td>{esc(c['name'])}</td><td>{'PASS' if c['ok'] else 'FAIL'}</td>"
        f"<td>{esc(c['detail'])}</td></tr>"
        for c in report.get("checks", [])
    )
    fields = (
        "run_id",
        "mode",
        "result",
        "backup_id",
        "cluster_id",
        "restore_seconds",
        "verification_seconds",
        "recovered_data_age_seconds",
        "cleanup",
        "cleanup_seconds",
        "phase",
        "verification_result",
        "error",
    )
    details = "".join(f"<dt>{esc(k)}</dt><dd>{esc(report[k])}</dd>" for k in fields if k in report)
    stamp = datetime.fromtimestamp(
        report.get("finished_at", report.get("updated_at", report.get("started_at", 0))),
        timezone.utc,
    ).isoformat()
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Бэкап под проверкой — {esc(report.get("result", "UNKNOWN"))}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:40px auto;padding:0 24px;color:#17212b}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #cdd5df;padding:10px;text-align:left}}
dd{{overflow-wrap:anywhere}}dt{{font-weight:700}}h1{{line-height:1.2}}</style>
<h1>Бэкап под проверкой: {esc(report.get("result", "UNKNOWN"))}</h1>
<p>{esc(stamp)}</p><dl>{details}</dl>
<table><caption>Результаты проверки</caption><thead><tr><th>Проверка</th><th>Результат</th>
<th>Описание</th></tr></thead><tbody>{rows}</tbody></table>
<p>Возраст данных не равен измеренному RPO. Время этого опыта не является гарантией RTO.
Локальная демонстрация не подтверждает работоспособность облачных API.</p></html>"""
