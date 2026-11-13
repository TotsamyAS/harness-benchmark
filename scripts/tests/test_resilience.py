import json
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError

from scripts.benchlib import runner
from scripts.benchlib.core import MODELS, Settings, Trace
from scripts.benchlib.fixtures import generate
from scripts.benchlib.privacy import prepare_advice, restore_advice, decrypt_record
from scripts.benchlib.provider import RouterClient, RequestPaused
from scripts.benchlib.runner import Benchmark, events
from scripts.benchlib.tools import FileTools
from scripts.tests.test_benchmark import response, call


def connection_error():
    cause = httpx.ConnectError("offline")
    error = APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    error.__cause__ = cause
    return error


def test_waits_then_recovers_without_loading_credentials(tmp_path):
    attempts, sleeps = [], []
    def transport(payload):
        attempts.append(1)
        if len(attempts) <= 2:
            raise connection_error()
        return response({"content": "back online"})
    client = RouterClient(Settings(tmp_path, live=True), transport, sleeper=sleeps.append)
    client.complete({"model": MODELS[0], "messages": []}, "small", Trace(tmp_path / "trace", {}))
    assert sleeps == [5, 10]
    assert len(attempts) == 3
    assert not list(client.cache.glob("*.pending.json"))


def test_rate_limit_retry_after_and_fatal_auth(tmp_path):
    request = httpx.Request("POST", "https://example.invalid")
    attempts, waits = [], []
    def transport(payload):
        attempts.append(1)
        if len(attempts) == 1:
            raise RateLimitError("rate limit", response=httpx.Response(429, request=request, headers={"retry-after": "12"}), body=None)
        return response({"content": "ok"})
    client = RouterClient(Settings(tmp_path, live=True), transport, sleeper=waits.append)
    client.complete({"model": MODELS[0]}, "small", Trace(tmp_path / "rate", {}))
    assert waits == [12]
    def unauthorized(payload):
        raise AuthenticationError("denied", response=httpx.Response(401, request=request), body=None)
    client = RouterClient(Settings(tmp_path, live=True), unauthorized, sleeper=lambda _: pytest.fail("Auth must not retry"))
    with pytest.raises(RuntimeError, match="not retried"):
        client.complete({"model": MODELS[0], "messages": []}, "small", Trace(tmp_path / "auth", {}))
    assert client.live_calls == 1


def test_lost_response_requires_explicit_resend_even_after_restart(tmp_path):
    payload = {"model": MODELS[0]}
    attempts = []
    def lost(p):
        attempts.append(1)
        raise APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    client = RouterClient(Settings(tmp_path, live=True), lost)
    with pytest.raises(RequestPaused, match="may be billed"):
        client.complete(payload, "small", Trace(tmp_path / "one", {}))
    restarted = RouterClient(Settings(tmp_path, live=True), lambda p: pytest.fail("No duplicate send"))
    with pytest.raises(RequestPaused, match="Previous send"):
        restarted.complete(payload, "small", Trace(tmp_path / "two", {}))
    retry = RouterClient(Settings(tmp_path, live=True, retry_ambiguous=True), lambda p: response({"content": "explicit resend"}))
    assert retry.complete(payload, "small", Trace(tmp_path / "three", {}))["choices"]
    assert len(attempts) == 1


def test_resume_within_multi_tool_reply_does_not_repeat_paid_calls(tmp_path):
    generate(tmp_path)
    attempts = []
    def transport(payload):
        attempts.append(payload["model"])
        if payload["model"].startswith("deepseek/"):
            if len(attempts) == 2:
                raise connection_error()
            return response({"content": "advice"})
        if not any(m["role"] == "tool" for m in payload["messages"]):
            first = call("write_file", {"path": "output/done.txt", "data_json": '{"text":"first tool finished"}'})
            first["tool_calls"].extend(call("ask_advisor", {"topic": "aggregation", "question": "ООО Тест: 100 RUB", "purpose": "compare"}, "second")["tool_calls"])
            return response(first, "tool_calls")
        return response({"content": "completed"})
    settings = Settings(tmp_path, live=True, network_retries=0)
    first = Benchmark(settings, transport).run("BENCH-04", MODELS[0], "protected", "resume")
    assert first["status"] == "paused"
    folder = tmp_path / first["folder"]
    assert (folder / "files/done.txt").exists()
    second = Benchmark(settings, transport).run("BENCH-04", MODELS[0], "protected", "resume")
    assert second["run_id"] == first["run_id"] and second["status"] == "needs_review"
    assert second["duration_s"] >= first["duration_s"]
    history = events(folder)
    assert sum(e["event"] == "tool_start" and e["tool"] == "write_file" for e in history) == 1
    assert attempts == [MODELS[0], settings.advisor_model, settings.advisor_model, MODELS[0]]
    assert any(e["event"] == "run_resume" for e in history)


def test_interrupt_partial_file_is_rolled_back_then_replayed(tmp_path, monkeypatch):
    generate(tmp_path)
    def transport(payload):
        if not any(m["role"] == "tool" for m in payload["messages"]):
            return response(call("write_file", {"path": "output/a.txt", "data_json": '{"text":"complete"}'}), "tool_calls")
        return response({"content": "done"})
    original = FileTools.execute
    def interrupted(self, name, args):
        (self.output / "a.txt").write_text("PARTIAL")
        raise KeyboardInterrupt()
    monkeypatch.setattr(FileTools, "execute", interrupted)
    settings = Settings(tmp_path, live=True)
    with pytest.raises(KeyboardInterrupt):
        Benchmark(settings, transport).run("BENCH-04", MODELS[0], "local", "interrupt")
    monkeypatch.setattr(FileTools, "execute", original)
    calls = []
    def after_restart(payload):
        calls.append(payload)
        return transport(payload)
    result = Benchmark(settings, after_restart).run("BENCH-04", MODELS[0], "local", "interrupt")
    assert result["status"] == "needs_review" and len(calls) == 1
    assert (tmp_path / result["folder"] / "files/a.txt").read_text() == "complete"


def test_changed_prompt_does_not_resume_old_state(tmp_path, monkeypatch):
    generate(tmp_path)
    settings = Settings(tmp_path, live=True, network_retries=0)
    def disconnected(payload):
        raise connection_error()
    first = Benchmark(settings, disconnected).run("BENCH-01", MODELS[0], "local", "prompt")
    monkeypatch.setattr(runner, "SYSTEM", runner.SYSTEM+"\nИзменённая инструкция.")
    second = Benchmark(settings, lambda p: response({"content": "done"})).run("BENCH-01", MODELS[0], "local", "prompt")
    assert first["run_id"] != second["run_id"]


def test_affine_pseudonyms_encrypted_mapping_and_restoration(tmp_path):
    question = "ООО Синтетик-Восток, Мария Тест, test@example.invalid: 15 000 RUB и 20 000 RUB"
    guard = prepare_advice("protected", {"question": question}, [], policy="pseudonymize_affine", vault_dir=tmp_path)
    text = guard["question"]
    assert "Синтетик" not in text and "Мария" not in text and "example.invalid" not in text
    assert "15 000" not in text and "20 000" not in text
    assert "a" not in guard and "b" not in guard
    record = decrypt_record(tmp_path, guard["vault_id"])
    assert float(record["a"]) > 0 and float(record["b"]) != 0
    assert restore_advice(text, tmp_path, guard["vault_id"]) == question
    encrypted = (tmp_path / f"{guard['vault_id']}.json").read_text()
    assert "Мария" not in encrypted and "15000" not in encrypted
    again = prepare_advice("protected", {"question": question}, [], policy="pseudonymize_affine", vault_dir=tmp_path)
    assert again["question"] == text
    assert restore_advice("[NUM_FFFFFFFFFFFFFFFF|1234]", tmp_path, guard["vault_id"]) == "[NUM_FFFFFFFFFFFFFFFF|1234]"


def test_semantic_document_uses_fallback_not_affine_illusion(tmp_path):
    question = "Секретная конструкция: новый канал охлаждения"
    guard = prepare_advice("protected", {"question": question, "topic": "translation"}, [],
                           policy="pseudonymize_affine", vault_dir=tmp_path, semantic_sensitive=True)
    assert guard["semantic_fallback"] and not guard["free_text_forwarded"]
    assert question not in guard["question"]


def test_chinese_contact_in_separate_excel_cell(tmp_path):
    question = '[{"cell":"A1","value":"联系人"},{"cell":"B1","value":"陈合成"}]'
    guard = prepare_advice("protected", {"question": question}, [],
                           policy="pseudonymize_affine", vault_dir=tmp_path)
    assert "陈合成" not in guard["question"]
    assert restore_advice(guard["question"], tmp_path, guard["vault_id"]) == question
