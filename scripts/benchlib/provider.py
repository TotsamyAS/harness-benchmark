"""Explicit live API access, persistent exact-request cache and complete call telemetry."""
from __future__ import annotations

import os
import time
from pathlib import Path

from .core import Settings, Trace, digest, read_json, write_json, VERSION


class RequestPaused(RuntimeError):
    """Pending request can be resumed; ambiguous sends require explicit permission."""


def retry_kind(exc):
    import httpx
    from openai import APIConnectionError, APITimeoutError, APIStatusError
    if isinstance(exc, APIStatusError):
        if exc.status_code == 429:
            return "safe"
        return "ambiguous" if exc.status_code >= 500 else "fatal"
    cause = exc
    while cause is not None:
        if isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, ConnectionRefusedError)):
            return "safe"
        cause = cause.__cause__
    if isinstance(exc, (APIConnectionError, APITimeoutError, httpx.TransportError)):
        return "ambiguous"
    return "fatal"


class RouterClient:
    def __init__(self, settings: Settings, transport=None, sleeper=time.sleep):
        self.settings = settings
        self.transport = transport
        self.client = None
        self.live_calls = 0
        self.sleeper = sleeper
        self.cache = settings.root / "bemnchmark" / "_cache" / "llm"

    def _call(self, payload):
        if self.transport:
            return self.transport(payload)
        if self.client is None:
            # The agent never inspects .env. Only this runtime path loads credentials.
            from dotenv import load_dotenv
            from openai import OpenAI
            load_dotenv(self.settings.root / ".env", override=False)
            key = os.environ.get("ROUTERAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("Set ROUTERAI_API_KEY in .env; its value is never displayed")
            self.client = OpenAI(api_key=key, base_url="https://routerai.ru/api/v1",
                                 timeout=self.settings.timeout, max_retries=0)
        return self.client.chat.completions.create(**payload).model_dump(mode="json")

    def complete(self, payload: dict, role: str, trace: Trace, cache_context=None):
        key = digest({"version": VERSION, "endpoint": "https://routerai.ru/api/v1", "payload": payload,
                      "context": cache_context})
        path = self.cache / f"{key}.json"
        pending = self.cache / f"{key}.pending.json"
        started = time.perf_counter()
        request_id = trace.event("llm_request", role=role, cache_key=key, request=payload,
                                 cache_context=cache_context)
        if path.exists() and not self.settings.force_refresh:
            record = read_json(path)
            if record["request_hash"] != key or digest(record["response"]) != record["response_hash"]:
                raise RuntimeError("Corrupt LLM cache")
            response = record["response"]
            pending.unlink(missing_ok=True)
            trace.event("llm_response", role=role, parent_id=request_id, cache_key=key,
                        source="cache", duration_s=time.perf_counter()-started,
                        original_duration_s=record["duration_s"], response=response,
                        billed_this_run=False, estimated_cost_rub=0,
                        original_estimated_cost_rub=record.get("estimated_cost_rub"))
            return response
        if pending.exists() and not self.settings.retry_ambiguous:
            trace.event("llm_paused", role=role, parent_id=request_id, reason="unconfirmed_previous_send")
            raise RequestPaused("Previous send has no confirmed response. Check provider billing; set retry_ambiguous=True explicitly to resend")
        if not self.settings.live:
            trace.event("llm_blocked", role=role, parent_id=request_id, reason="offline_cache_miss")
            raise RuntimeError("Offline cache miss. Enable live=True explicitly to call models")
        from .inference import validate_request
        if self.transport is None:
            checks = validate_request(payload, self.settings.root)
            trace.event("request_preflight", role=role, checks=checks)
        for attempt in range(self.settings.network_retries+1):
            if self.settings.max_calls is not None and self.live_calls >= self.settings.max_calls:
                raise RequestPaused("Session API call limit reached; checkpoint retained")
            self.live_calls += 1
            write_json(pending, {"request_hash": key, "attempt": attempt, "role": role})
            trace.event("llm_attempt", role=role, parent_id=request_id, attempt=attempt+1, cache_key=key)
            try:
                response = self._call(payload)
                break
            except Exception as exc:
                kind = retry_kind(exc)
                trace.event("llm_error", role=role, parent_id=request_id, attempt=attempt+1,
                            error_type=type(exc).__name__, retry_kind=kind,
                            duration_s=time.perf_counter()-started)
                if kind in {"safe", "fatal"}:
                    pending.unlink(missing_ok=True)
                if kind == "fatal":
                    raise RuntimeError(f"API call failed ({type(exc).__name__}); not retried") from None
                if kind == "ambiguous" and not self.settings.retry_ambiguous:
                    raise RequestPaused("Response lost or server error: request may be billed. Check account before explicit retry_ambiguous=True") from None
                if attempt >= self.settings.network_retries:
                    raise RequestPaused("Network retry limit reached; checkpoint retained") from None
                delay = min(self.settings.retry_max_s, self.settings.retry_initial_s * 2**attempt)
                retry_after = getattr(exc, "response", None)
                if retry_after is not None:
                    try:
                        delay = min(self.settings.retry_max_s, max(delay, float(retry_after.headers.get("retry-after", 0))))
                    except (ValueError, TypeError):
                        pass
                trace.event("network_wait", role=role, parent_id=request_id, delay_s=delay,
                            attempt=attempt+1, ambiguous=kind == "ambiguous")
                print(f"Network wait: {delay:g}s; retry {attempt+1}/{self.settings.network_retries}")
                self.sleeper(delay)
        elapsed = time.perf_counter()-started
        estimate = self.estimate_cost(payload["model"], response.get("usage") or {})
        write_json(path, {"request_hash": key, "response_hash": digest(response),
                          "response": response, "duration_s": elapsed, "estimated_cost_rub": estimate})
        pending.unlink(missing_ok=True)
        trace.event("llm_response", role=role, parent_id=request_id, source="live", cache_key=key,
                    duration_s=elapsed, response=response, billed_this_run=True,
                    estimated_cost_rub=estimate)
        return response

    def estimate_cost(self, model, usage):
        snapshot = self.settings.root / "docs" / "routerai-models-2026-11-12.json"
        if not snapshot.exists():
            return None
        catalog = read_json(snapshot)
        entry = next((m for m in catalog.get("data", []) if m["id"] == model), None)
        if not entry:
            return None
        price = entry["pricing"]
        # Conservative list-price estimate; actual routed/cached input billing can differ.
        return (usage.get("prompt_tokens", 0) or 0)*float(price["prompt"]) + (usage.get("completion_tokens", 0) or 0)*float(price["completion"])
