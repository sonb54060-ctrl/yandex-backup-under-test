"""Create reproducible function archives, excluding local credentials and development files."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
build = ROOT / "build"
build.mkdir(exist_ok=True)
config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
with urlopen("https://storage.yandexcloud.net/cloud-certs/CA.pem", timeout=20) as response:
    certificate = response.read()
if b"BEGIN CERTIFICATE" not in certificate:
    raise ValueError("Invalid CA certificate")
files = {
    str(p.relative_to(ROOT / "src")).replace("\\", "/"): p.read_bytes()
    for p in (ROOT / "src" / "backup_lab").glob("*.py")
}
files["backup_lab/certs/root.crt"] = certificate
files["config.json"] = json.dumps(config, sort_keys=True).encode()
files["requirements.txt"] = (ROOT / "requirements.txt").read_bytes()
archive = build / "function.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
    for name, data in sorted(files.items()):
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        target.writestr(info, data)
print(
    json.dumps(
        {"archive": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    )
)
