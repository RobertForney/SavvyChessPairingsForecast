"""Download and verify the pinned official BBP Windows build locally."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def setup():
    pinned = json.loads((ROOT / "app" / "engine-manifest.json").read_text(encoding="utf-8"))
    if os.name != "nt":
        raise ValueError("This pinned setup currently supports Windows x64 only.")
    destination = ROOT / pinned["executable"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive_path = destination.parent / "bbp-windows.zip"
    data = archive_path.read_bytes() if archive_path.exists() else None
    if data is None or hashlib.sha256(data).hexdigest() != pinned["archiveSha256"]:
        request = urllib.request.Request(pinned["archiveUrl"], headers={"User-Agent": "SavvyPairings engine setup"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(pinned["archiveBytes"] + 1)
        if len(data) != pinned["archiveBytes"] or hashlib.sha256(data).hexdigest() != pinned["archiveSha256"]:
            raise ValueError("Official engine archive does not match the pinned size and SHA-256. Nothing was installed.")
        archive_path.write_bytes(data)
    expected = {"bbpPairings.exe", "LICENSE.txt", "Apache-2.0.txt", "README.txt"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {}
        for entry in archive.infolist():
            basename = Path(entry.filename).name
            if basename in expected:
                if basename in files or entry.file_size > 10_000_000:
                    raise ValueError("Official archive contains ambiguous or oversized engine files.")
                files[basename] = archive.read(entry)
        if set(files) != expected:
            raise ValueError("Official archive is missing required executable or license files.")
    if hashlib.sha256(files["bbpPairings.exe"]).hexdigest() != pinned["executableSha256"]:
        raise ValueError("Executable checksum does not match the pinned build. Nothing was installed.")
    for name, content in files.items():
        temporary = destination.parent / (name + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination.parent / name)
    print(f"Verified BBP Pairings {pinned['version']} installed at {destination}")


if __name__ == "__main__":
    try:
        setup()
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"Engine setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
