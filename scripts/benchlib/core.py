from __future__ import annotations

import hashlib
import json
import os
import platform
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1"
MODELS = ["google/gemma-4-26b-a4b-it", "openai/gpt-oss-20b", "mistralai/ministral-14b-2512", "qwen/qwen3.5-9b"]
MODES = ["local", "self_guard", "protected"]
ADVISOR = "deepseek/deepseek-v4.1-flash"
COLORS = dict(zip(MODELS, ["#9DD7F5", "#FFFFFF", "#F39A36", "#B899DE"]))
HATCHES = {"local": "", "self_guard": "///", "protected": "xx"}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def inventory(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Path must be a file inside the assigned directory")
    if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root.resolve())):
        raise ValueError("Symlinks are not allowed")
    return path


@dataclass
class Settings:
    root: Path
    live: bool = False
    max_steps: int = 30
    max_calls: int | None = None
    max_tokens: int | None = None
    timeout: float = 180
    network_retries: int = 12
    retry_initial_s: float = 5
    retry_max_s: float = 30
    retry_ambiguous: bool = False
    advisor_model: str = ADVISOR
    judge_model: str = ADVISOR
    # A reviewed allowlist contains generic assistance only, no document excerpts.
    guard_policy: str = "pseudonymize_affine"
    force_refresh: bool = False
    model_parameters: dict | None = None

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        for name in ["max_steps", "max_calls", "max_tokens"]:
            if name != "max_steps" and getattr(self, name) is None:
                continue
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        for parameters in (self.model_parameters or {}).values():
            if set(parameters) & {"model", "messages", "tools", "max_tokens", "max_completion_tokens", "stream", "n"}:
                raise ValueError("model_parameters cannot override model, history, tools or output limits")
        if self.guard_policy not in {"generic_only", "pseudonymize_affine"}:
            raise ValueError("Unknown privacy policy")
        if self.network_retries < 0 or not 0 <= self.retry_initial_s <= self.retry_max_s <= 60:
            raise ValueError("Invalid network retry settings")


class Trace:
    def __init__(self, folder: Path, context: dict):
        self.path = folder / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.context = context
        prior = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        self.seq = json.loads(prior[-1])["event_id"] if prior else 0
        self.elapsed_offset = json.loads(prior[-1])["elapsed_s"] if prior else 0
        self.session_id = uuid.uuid4().hex
        self.started = time.perf_counter()

    def event(self, kind: str, **data):
        self.seq += 1
        event = {**self.context, "event_id": self.seq, "event": kind,
                 "trace_session_id": self.session_id,
                 "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                 "elapsed_s": round(self.elapsed_offset + time.perf_counter() - self.started, 6), **data}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(canonical(event) + "\n")
        return self.seq


def environment():
    from importlib.metadata import version, PackageNotFoundError
    packages = {}
    for name in ["openai", "openpyxl", "python-docx", "python-pptx", "pypdf", "reportlab", "cryptography"]:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "missing"
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": packages}
