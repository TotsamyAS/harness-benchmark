"""Observable task mining; reasoning traces are only what the provider actually returns."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

from .core import read_json, write_json
from .review import report_dir
from .runner import batch_runs, events


def activity(event):
    kind = event["event"]
    if kind in {"llm_request", "llm_response", "llm_error"}:
        return f"{event.get('role')}:{kind}"
    if kind in {"tool_start", "tool_end", "tool_error"}:
        return f"{event.get('tool')}:{kind}"
    if kind == "advisor_gate":
        return "advisor:"+("allowed" if event["decision"]["allowed"] else "blocked")
    return kind


def export_mining(root: Path, batch_id):
    root = Path(root)
    folder = report_dir(root, batch_id)
    folder.mkdir(parents=True, exist_ok=True)
    rows, summaries, judge_rows, judge_summaries, edges = [], [], [], [], Counter()
    selected_ids = {read_json(f / "result.json")["run_id"] for f in batch_runs(root, batch_id)}
    for run_folder in batch_runs(root, batch_id, all_attempts=True):
        result = read_json(run_folder / "result.json")
        history = events(run_folder)
        counts = Counter(e["event"] for e in history)
        responses = [e for e in history if e["event"] == "llm_response"]
        usage = Counter()
        for e in responses:
            u = e["response"].get("usage") or {}
            prefix = "live" if e["source"] == "live" else "replayed"
            for field in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                usage[f"{prefix}_{field}"] += u.get(field, 0) or 0
        summaries.append({k: result[k] for k in ["run_id", "case_id", "model", "mode", "status", "duration_s"]} | {
            "steps": counts["step_start"], "tool_calls": counts["tool_start"],
            "selected_attempt": result["run_id"] in selected_ids,
            "tool_errors": counts["tool_error"]+counts["tool_argument_error"],
            "network_retries": counts["network_wait"], "resumptions": counts["run_resume"],
            "affine_redactions": sum(e["event"] == "advisor_gate" and e["decision"].get("policy") == "pseudonymize_affine" for e in history),
            "advisor_requests": sum(e["event"] == "llm_request" and e.get("role") == "advisor" for e in history),
            "advisor_blocked": sum(e["event"] == "advisor_gate" and not e["decision"]["allowed"] for e in history),
            "generic_rewrites": sum(e["event"] == "advisor_gate" and e["decision"].get("policy") == "generic_only" for e in history),
            "live_llm_calls": sum(e["source"] == "live" for e in responses),
            "cached_llm_calls": sum(e["source"] == "cache" for e in responses),
            "cached_tools": sum(e["event"] == "tool_end" and e.get("source") == "cache" for e in history),
            "estimated_live_cost_rub": sum(e.get("estimated_cost_rub") or 0 for e in responses),
            "cost_missing_calls": sum(e.get("estimated_cost_rub") is None and e["source"] == "live" for e in responses),
            **usage})
        previous = None
        for event in history:
            label = activity(event)
            if previous is not None:
                edges[(result["model"], result["mode"], previous, label)] += 1
            previous = label
            rows.append({"run_id": result["run_id"], "case_id": result["case_id"],
                         "model": result["model"], "mode": result["mode"], "event_id": event["event_id"],
                         "timestamp_utc": event["timestamp_utc"], "elapsed_s": event["elapsed_s"],
                         "activity": label, "source": event.get("source"), "duration_s": event.get("duration_s"),
                         "parent_id": event.get("parent_id"), "details_json": json.dumps(event, ensure_ascii=False)})
        evaluation = run_folder / "evaluation/events.jsonl"
        if evaluation.exists():
            eval_events = [json.loads(line) for line in evaluation.read_text(encoding="utf-8").splitlines()]
            judge_rows.extend(eval_events)
            calls = [e for e in eval_events if e["event"] == "llm_response"]
            judge_summaries.append({"run_id": result["run_id"], "case_id": result["case_id"],
                                    "live_calls": sum(e["source"] == "live" for e in calls),
                                    "cached_calls": sum(e["source"] == "cache" for e in calls),
                                    "estimated_live_cost_rub": sum(e.get("estimated_cost_rub") or 0 for e in calls)})
    pd.DataFrame(rows).to_csv(folder / "task_mining_events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(summaries).fillna(0).to_csv(folder / "task_mining_runs.csv", index=False, encoding="utf-8-sig")
    edge_records = [{"model": m, "mode": mode, "from": a, "to": b, "count": count}
                    for (m, mode, a, b), count in edges.items()]
    write_json(folder / "task_mining_transitions.json", edge_records)
    write_json(folder / "judge_events.json", judge_rows)
    pd.DataFrame(judge_summaries).to_csv(folder / "judge_costs.csv", index=False, encoding="utf-8-sig")
    # Full per-run JSONL remains authoritative; CSV is an analysis-friendly projection.
    return pd.DataFrame(summaries).fillna(0)
