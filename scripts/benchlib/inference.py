"""Local request/protocol checks. No credentials and no inference requests."""
import json
from pathlib import Path

from .core import canonical, read_json


def validate_request(payload, root):
    if not isinstance(payload.get("model"), str):
        raise ValueError("A model ID is required")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("A non-empty message history is required")
    pending = set()
    for message in messages:
        role = message.get("role")
        if role == "tool":
            identifier = message.get("tool_call_id")
            if identifier not in pending:
                raise ValueError("Orphan or duplicate tool response")
            pending.remove(identifier)
        else:
            if pending:
                raise ValueError("Missing tool responses before next message")
            if role == "assistant":
                ids = [c.get("id") for c in message.get("tool_calls", [])]
                if any(not i for i in ids) or len(ids) != len(set(ids)):
                    raise ValueError("Invalid tool call IDs")
                pending.update(ids)
    if pending:
        raise ValueError("Unfinished tool calls in request history")
    limit = payload.get("max_tokens", payload.get("max_completion_tokens"))
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("Positive output-token budget required")
    catalog_path = Path(root) / "docs/routerai-models-2026-11-12.json"
    if not catalog_path.exists():
        return {"catalog_verified": False}
    entry = next((m for m in read_json(catalog_path).get("data", []) if m["id"] == payload["model"]), None)
    if not entry:
        raise ValueError("Model absent from saved catalog; refresh catalog before paid calls")
    supported = set(entry.get("supported_parameters", []))
    for name in payload:
        if name not in {"model", "messages"} and name not in supported:
            raise ValueError(f"Parameter not advertised by model: {name}")
    # Conservative UTF-8 byte upper bound, NOT exact tokenizer accounting.
    upper = len(canonical({k: v for k, v in payload.items() if k in {"messages", "tools"}}).encode("utf-8"))
    context = entry.get("context_length")
    if context and upper+(limit or 0) > context:
        raise ValueError("Conservative context bound exceeded; paginate/reduce history before calling")
    return {"catalog_verified": True, "input_token_upper_bound": upper, "output_tokens": limit,
            "context_length": context, "method": "UTF-8 bytes, conservative; no exact tokenizer"}


def validate_response(response):
    choices = response.get("choices") if isinstance(response, dict) else None
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise ValueError("Missing assistant completion; response retained in cache for diagnosis")
    choice = choices[0]
    if choice.get("finish_reason") not in {"stop", "tool_calls"}:
        raise ValueError(f"Incomplete/refused generation: {choice.get('finish_reason')}; do not execute tool calls")
    message = choice["message"]
    calls = message.get("tool_calls") or []
    if calls:
        identifiers = [c.get("id") for c in calls]
        if any(not i for i in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate or missing tool call IDs")
        for call in calls:
            f = call.get("function") or {}
            if call.get("type") != "function" or not isinstance(f.get("name"), str) or not isinstance(f.get("arguments"), str):
                raise ValueError("Invalid function call envelope")
    elif not isinstance(message.get("content"), str) or not message["content"].strip():
        raise ValueError("Empty assistant response")
    return choice
