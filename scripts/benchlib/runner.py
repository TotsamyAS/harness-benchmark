from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .core import MODES, MODELS, Settings, Trace, canonical, digest, environment, inventory, read_json, write_json
from .privacy import prepare_advice
from .provider import RouterClient
from .tools import ADVICE_TOOL, TOOLS, FileTools

from .prompts import SYSTEM



def load_cases(root: Path):
    return [read_json(p) for p in sorted((root / "bemnchmark").glob("BENCH-*/case.json"))]


def preflight(settings: Settings):
    import importlib.util
    problems = []
    for module in ["openai", "dotenv", "openpyxl", "docx", "pptx", "pypdf", "reportlab", "cryptography"]:
        if importlib.util.find_spec(module) is None:
            problems.append(f"Missing dependency: {module}")
    cases = load_cases(settings.root)
    if len(cases) != 17:
        problems.append(f"Expected 17 cases, found {len(cases)}")
    for case in cases:
        actual = inventory(settings.root / "bemnchmark" / case["id"] / "input")
        if actual != case["input_hashes"]:
            problems.append(f"{case['id']}: input changed; review and update case.json hashes explicitly")
    return {"ready": not problems, "problems": problems, "cases": len(cases),
            "models": MODELS, "modes": MODES, "planned_runs": len(cases)*len(MODELS)*len(MODES),
            "live_enabled": settings.live, "credentials_inspected": False,
            "guard_policy": settings.guard_policy}


class Benchmark:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.client = RouterClient(settings, transport)

    def configuration(self):
        return {"max_steps": self.settings.max_steps, "max_tokens": self.settings.max_tokens,
                "parameters": self.settings.model_parameters, "advisor": self.settings.advisor_model,
                "policy": self.settings.guard_policy, "system": SYSTEM, "tools": TOOLS+[ADVICE_TOOL],
                "implementation": {p.name: digest(p.read_text(encoding="utf-8"))
                                   for p in sorted(Path(__file__).parent.glob("*.py"))},
                "environment": environment()}

    def run(self, case_id, model, mode, batch_id, repetition=0):
        from . import checkpoint
        from .provider import RequestPaused
        if model not in MODELS or mode not in MODES:
            raise ValueError("Unapproved model or mode")
        if not batch_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in batch_id):
            raise ValueError("Use a simple alphanumeric batch ID")
        case_dir = self.settings.root / "bemnchmark" / case_id
        case = read_json(case_dir / "case.json")
        if inventory(case_dir / "input") != case["input_hashes"]:
            raise RuntimeError("Input fingerprint changed; review manifest first")
        config = self.configuration()
        signature = digest({"case": case, "configuration": config})
        parent = case_dir / "output" / model.replace("/", "__") / mode
        blob_dir = self.settings.root / "bemnchmark/_cache/checkpoint_blobs"
        state, folder = None, None
        if not self.settings.force_refresh:
            for candidate in sorted(parent.glob("*/checkpoint.json"), reverse=True):
                saved = checkpoint.load(candidate.parent)
                ctx = saved["context"]
                if (ctx["batch_id"] == batch_id and ctx["repetition"] == repetition
                    and saved["signature"] == signature
                    and (saved["phase"] != "done" or not (candidate.parent / "result.json").exists())):
                    state, folder = saved, candidate.parent
                    checkpoint.restore(folder, state, blob_dir)
                    break
        resumed = state is not None
        if not resumed:
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")+"-"+uuid.uuid4().hex[:8]
            folder = parent / run_id
            (folder / "files").mkdir(parents=True)
            if case_id == "BENCH-15":
                assets = self.settings.root / "bemnchmark/_assets"
                if not all((assets / n).exists() for n in ["konva.min.js", "lucide.min.js"]):
                    raise RuntimeError("Run scripts.prepare_assets before BENCH-15")
                shutil.copytree(assets, folder / "files/assets")
            state = {"context": {"case_id": case_id, "model": model, "mode": mode,
                                  "batch_id": batch_id, "run_id": run_id, "repetition": repetition},
                     "signature": signature, "phase": "model", "step": 0, "tool_index": 0,
                     "tool_in_progress": False, "status": "running", "final": "", "duration_s": 0,
                     "messages": [{"role": "system", "content": SYSTEM},
                                  {"role": "user", "content": case["instruction"]+"\n"+case["privacy_policy"]}]}
        trace = Trace(folder, state["context"])
        trace.event("run_resume" if resumed else "run_start", step=state["step"], phase=state["phase"],
                    environment=environment(), inputs=case["input_hashes"], case_hash=digest(case),
                    guard_policy=self.settings.guard_policy, checkpoint_signature=signature)
        tools = FileTools(case_dir / "input", folder / "files", self.settings.root / "bemnchmark/_cache/tools",
                          trace, self.settings.force_refresh)
        toolset = TOOLS + ([] if mode == "local" else [ADVICE_TOOL])
        cache_context = {"case_hash": digest(case), "input_hashes": case["input_hashes"]}
        def persist():
            checkpoint.save(folder, state, blob_dir)
        persist()
        started = time.perf_counter()
        if state["phase"] != "done":
            state["status"] = "running"
        try:
            while state["phase"] != "done":
                if state["phase"] == "model":
                    if state["step"] >= self.settings.max_steps:
                        state.update(phase="done", status="step_limit")
                        persist()
                        break
                    trace.event("step_start", step=state["step"])
                    payload = {"model": model, "messages": state["messages"], "tools": toolset,
                               "max_tokens": self.settings.max_tokens,
                               **(self.settings.model_parameters or {}).get(model, {})}
                    response = self.client.complete(payload, "small", trace, cache_context)
                    choice = response["choices"][0]
                    raw = choice["message"]
                    message = {k: v for k, v in raw.items() if k in
                               {"role", "content", "tool_calls", "reasoning", "reasoning_details"} and v is not None}
                    message.setdefault("role", "assistant")
                    state["messages"].append(message)
                    state["pending_calls"] = raw.get("tool_calls") or []
                    state["tool_index"] = 0
                    if not state["pending_calls"]:
                        state.update(final=raw.get("content") or "", phase="done",
                                     status="completed" if choice.get("finish_reason") == "stop" else "incomplete_generation")
                    else:
                        state["phase"] = "tools"
                    persist()  # Response is durable before any tool is executed.
                elif state["phase"] == "tools":
                    calls = state["pending_calls"]
                    while state["tool_index"] < len(calls):
                        call = calls[state["tool_index"]]
                        state["tool_in_progress"] = True
                        persist()  # Recovery point before a possibly interrupted file operation.
                        name = call["function"]["name"]
                        try:
                            args = json.loads(call["function"]["arguments"])
                            if name == "ask_advisor":
                                result = self.advice(mode, args, case, folder, trace, cache_context, call["id"])
                            elif name in {t["function"]["name"] for t in TOOLS}:
                                result = tools.execute(name, args)
                            else:
                                result = {"error": "Unknown or unavailable tool"}
                        except (ValueError, KeyError, TypeError) as exc:
                            result = {"error": type(exc).__name__, "message": str(exc)}
                            trace.event("tool_argument_error", tool_call_id=call["id"], result=result)
                        trace.event("tool_result", step=state["step"], tool_call_id=call["id"], tool=name, result=result)
                        state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": canonical(result)})
                        state["tool_index"] += 1
                        state["tool_in_progress"] = False
                        persist()
                    state["step"] += 1
                    state["phase"] = "model"
                    persist()
        except KeyboardInterrupt:
            state["status"] = "paused"
            trace.event("run_paused", reason="keyboard_interrupt", checkpoint_retained=True)
            raise
        except Exception as exc:
            state["status"] = "paused" if isinstance(exc, RequestPaused) else "error"
            trace.event("run_paused" if isinstance(exc, RequestPaused) else "run_error",
                        error_type=type(exc).__name__, message=str(exc), checkpoint_retained=True)
        finally:
            # If a tool was interrupted, retain its PRE-operation snapshot; do not snapshot partial writes.
            state["duration_s"] += time.perf_counter()-started
            if not state["tool_in_progress"]:
                persist()
            else:
                # Update status/time while retaining the PRE-tool file snapshot.
                write_json(folder / "checkpoint.json", {"state": state, "checksum": digest(state)})
            trace.event("run_end", status=state["status"], artifacts=inventory(folder / "files"))
            result = {**state["context"], "status": state["status"], "final": state["final"],
                      "artifacts": inventory(folder / "files"), "configuration": config,
                      "trace_digest": digest((folder / "events.jsonl").read_text(encoding="utf-8")),
                      "case_hash": digest(case), "input_hashes": case["input_hashes"],
                      "duration_s": state["duration_s"], "folder": folder.relative_to(self.settings.root).as_posix()}
            write_json(folder / "result.json", result)
            (folder / "final.md").write_text(state["final"], encoding="utf-8")
        return result

    def advice(self, mode, args, case, folder, trace, cache_context, tool_call_id):
        vault = self.settings.root / "bemnchmark/_cache/privacy" / digest(case)
        guard = prepare_advice(mode, args, case["secrets"], policy=self.settings.guard_policy,
                               vault_dir=vault, semantic_sensitive=case.get("semantic_sensitive", False))
        trace.event("advisor_gate", tool_call_id=tool_call_id, requested=args, decision=guard)
        if not guard["allowed"]:
            return {"blocked": True, "reason": guard["reason"]}
        payload = {"model": self.settings.advisor_model,
                   "messages": [{"role": "system", "content": "Provide concise practical advice. You have no access to files or other conversations. "+guard.get("advisor_instruction", "")},
                                {"role": "user", "content": guard["question"]}],
                   "max_tokens": min(3000, self.settings.max_tokens)}
        response = self.client.complete(payload, "advisor", trace, cache_context)
        text = response["choices"][0]["message"].get("content", "") or ""
        if guard.get("vault_id"):
            from .privacy import restore_advice
            text = restore_advice(text, vault, guard["vault_id"])
        return {"advice": text, "policy": guard["policy"]}

    def run_matrix(self, batch_id, case_ids=None, models=None, modes=None, repetitions=1):
        results = []
        existing = [read_json(folder / "result.json") for folder in batch_runs(self.settings.root, batch_id)]
        for model in models or MODELS:
            for case in load_cases(self.settings.root):
                if case_ids and case["id"] not in case_ids:
                    continue
                if inventory(self.settings.root / "bemnchmark" / case["id"] / "input") != case["input_hashes"]:
                    raise RuntimeError("Input fingerprint changed; review manifest first")
                for mode in modes or MODES:
                    for repetition in range(repetitions):
                        previous = next((r for r in existing if r["case_id"] == case["id"] and r["model"] == model
                                         and r["mode"] == mode and r["repetition"] == repetition
                                         and r["status"] == "completed" and r.get("case_hash") == digest(case)
                                         and r.get("configuration") == self.configuration()), None)
                        if previous and not self.settings.force_refresh:
                            old_folder = self.settings.root / previous["folder"]
                            if (inventory(old_folder / "files") == previous["artifacts"]
                                and digest((old_folder / "events.jsonl").read_text(encoding="utf-8")) == previous.get("trace_digest")):
                                print(f"Resume: {case['id']} | {model} | {mode} (saved run)")
                                results.append(previous)
                                continue
                        result = self.run(case["id"], model, mode, batch_id, repetition)
                        results.append(result)
                        print(f"{case['id']} | {model} | {mode} | {result['status']}")
                        if result["status"] in {"error", "paused"}:
                            # Stop on auth, budget and transport errors rather than burning a matrix.
                            return results
        return results


def events(folder: Path):
    return [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def batch_runs(root: Path, batch_id: str, all_attempts=False):
    candidates = []
    latest = {}
    for p in sorted((root / "bemnchmark").glob("BENCH-*/output/*/*/*/result.json")):
        record = read_json(p)
        if record["batch_id"] != batch_id:
            continue
        candidates.append(p.parent)
        key = (record["case_id"], record["model"], record["mode"], record["repetition"])
        latest[key] = p.parent
    return candidates if all_attempts else list(latest.values())
