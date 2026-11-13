"""Atomic agent state plus content-addressed snapshots of generated files."""
import hashlib
from pathlib import Path

from .core import digest, inventory, read_json, safe_path, write_json


def save(folder: Path, state: dict, blob_dir: Path):
    blob_dir.mkdir(parents=True, exist_ok=True)
    files = folder / "files"
    hashes = inventory(files)
    for relative, key in hashes.items():
        blob = blob_dir / key
        if not blob.exists():
            content = safe_path(files, relative).read_bytes()
            temporary = blob.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(blob)
    state["file_snapshot"] = hashes
    write_json(folder / "checkpoint.json", {"state": state, "checksum": digest(state)})


def load(folder: Path):
    record = read_json(folder / "checkpoint.json")
    if digest(record["state"]) != record["checksum"]:
        raise RuntimeError("Checkpoint integrity check failed")
    return record["state"]


def restore(folder: Path, state: dict, blob_dir: Path):
    files = folder / "files"
    wanted = state["file_snapshot"]
    if inventory(files) == wanted:
        return
    if not state.get("tool_in_progress"):
        raise RuntimeError("Files changed outside an interrupted tool; checkpoint resume refused")
    # Only an interrupted tool transaction can be rolled back, never input/.
    verified = {}
    for relative, key in wanted.items():
        content = (blob_dir / key).read_bytes()
        if hashlib.sha256(content).hexdigest() != key:
            raise RuntimeError("Checkpoint artifact blob is corrupt")
        verified[relative] = content
    for relative in inventory(files):
        if relative not in wanted:
            safe_path(files, relative).unlink()
    for relative, content in verified.items():
        target = safe_path(files, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
