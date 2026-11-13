"""Download pinned public JS libraries. No credentials, no model calls."""
from pathlib import Path
import hashlib
import urllib.request

from scripts.benchlib.core import write_json

ASSETS = {
    "konva.min.js": "https://cdn.jsdelivr.net/npm/konva@9.3.18/konva.min.js",
    "lucide.min.js": "https://cdn.jsdelivr.net/npm/lucide@0.468.0/dist/umd/lucide.min.js",
}


def prepare(root: Path):
    folder = root / "bemnchmark" / "_assets"
    folder.mkdir(parents=True, exist_ok=True)
    metadata = {}
    for name, url in ASSETS.items():
        target = folder / name
        if not target.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                content = response.read()
            target.write_bytes(content)
        metadata[name] = {"url": url, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    write_json(folder / "manifest.json", metadata)
    return metadata


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
