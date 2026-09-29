"""Construir una versión de código sin datos, y opcionalmente incluirla en captura.

El SHA-256 identifica bytes probados/promovidos. La configuración queda fuera.
"""

import argparse
import hashlib
import json
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    folder = args.capture or ROOT / "data/production/releases" / datetime.now(
        UTC
    ).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    paths = {ROOT / p for p in tracked if p and not p.startswith("data/")}
    paths.update(p for p in (ROOT / "scripts/production").glob("*") if p.is_file())
    archive_path = folder / "code.tar.gz"
    with tarfile.open(archive_path, "w:gz", compresslevel=1) as archive:
        for p in sorted(paths):
            if p.is_file() and not p.is_symlink():
                archive.add(p, arcname=p.relative_to(ROOT).as_posix(), recursive=False)
    with archive_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {"sha256": digest, "bytes": archive_path.stat().st_size}
    if args.capture:
        path = folder / "capture.json"
        capture = json.loads(path.read_text())
        capture["artifacts"]["code.tar.gz"] = result
        path.write_text(json.dumps(capture, indent=2) + "\n")
    else:
        (folder / "release.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"archive": str(archive_path), **result}))


if __name__ == "__main__":
    main()
