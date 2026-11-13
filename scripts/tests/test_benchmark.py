from pathlib import Path
import json

import pytest
from openpyxl import load_workbook

from scripts.benchlib.core import MODELS, Settings, Trace, inventory, read_json, write_json
from scripts.benchlib.documents import read_document, revise_contract, write_document
from scripts.benchlib.evaluation import judge_run
from scripts.benchlib.fixtures import generate
from scripts.benchlib.privacy import prepare_advice
from scripts.benchlib.provider import RouterClient
from scripts.benchlib.review import approve, approved_results, export_review
from scripts.benchlib.runner import Benchmark, events, preflight
from scripts.benchlib.tools import FileTools


def response(message, finish="stop"):
    return {"choices": [{"message": {"role": "assistant", **message}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def call(name, args, id="call1"):
    args = dict(args)
    for old, new in [("data_json", "data"), ("changes_json", "changes"), ("replacements_json", "replacements")]:
        if old in args:
            args[new] = json.loads(args.pop(old))
    return {"content": None, "tool_calls": [{"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_corpus_generation_and_hashes(tmp_path):
    cases = generate(tmp_path)
    assert len(cases) == 17
    assert preflight(Settings(tmp_path))["ready"]
    for case in cases:
        folder = tmp_path / "bemnchmark" / case["id"]
        assert inventory(folder / "input") == case["input_hashes"]
        for p in (folder / "input").iterdir():
            assert read_document(p)
    p = tmp_path / "bemnchmark/BENCH-01/input/CBD.xlsx"
    previous = p.read_bytes()
    generate(tmp_path)
    assert p.read_bytes() == previous


def test_cache_and_offline_never_load_credentials(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail("must not read .env"))
    calls = []
    client = RouterClient(Settings(tmp_path, live=True), lambda p: calls.append(p) or response({"content": "ok"}))
    payload = {"model": MODELS[0], "messages": [{"role": "user", "content": "same"}]}
    assert client.complete(payload, "small", Trace(tmp_path / "a", {}))["choices"]
    offline = RouterClient(Settings(tmp_path))
    offline.complete(payload, "small", Trace(tmp_path / "b", {}))
    assert len(calls) == 1
    with pytest.raises(RuntimeError, match="Offline cache miss"):
        offline.complete({**payload, "max_tokens": 2}, "small", Trace(tmp_path / "c", {}))


def test_semantic_guard_never_forwards_free_text():
    args = {"topic": "translation", "question": "Секретная схема с новым каналом, парафраз секрета", "purpose": "translate"}
    guarded = prepare_advice("protected", args, [])
    assert args["question"] not in guarded["question"]
    assert not guarded["free_text_forwarded"]
    assert prepare_advice("self_guard", args, [])["question"] == args["question"]
    assert not prepare_advice("local", args, [])["allowed"]
    assert not prepare_advice("protected", {"topic": "anything"}, [])["allowed"]


def test_tool_cache_restores_binary_files_and_rejects_escape(tmp_path):
    inp, out = tmp_path / "input", tmp_path / "out"
    inp.mkdir()
    trace = Trace(tmp_path / "trace", {})
    tools = FileTools(inp, out, tmp_path / "cache", trace)
    args = {"path": "output/test.xlsx", "data_json": json.dumps({"sheets": {"Data": [["x", "y"], [1, {"formula": "=A2*2"}]]}})}
    tools.execute("write_file", args)
    binary = (out / "test.xlsx").read_bytes()
    (out / "test.xlsx").unlink()
    tools.execute("write_file", args)
    assert (out / "test.xlsx").read_bytes() == binary
    assert events(tmp_path / "trace")[-1]["source"] == "cache"
    error = tools.execute("write_file", {"path": "output/../../escape.txt", "data_json": '{"text":"bad"}'})
    assert "error" in error
    assert not (tmp_path / "escape.txt").exists()
    assert "error" in tools.execute("write_file", {"path": "input/overwrite.txt", "data_json": '{"text":"bad"}'})


def test_word_revisions_and_comments(tmp_path):
    source, target = tmp_path / "in.docx", tmp_path / "out.docx"
    write_document(source, {"title": "Договор", "paragraphs": ["Аванс составляет 80% цены договора."]})
    revise_contract(source, target, [{"old": "80%", "new": "30%", "reason": "Превышен лимит", "policy_ref": "П.1"}])
    import zipfile
    with zipfile.ZipFile(target) as z:
        xml = z.read("word/document.xml").decode()
        assert "w:ins" in xml and "w:del" in xml
        assert "comments.xml" in " ".join(z.namelist())
    extracted = read_document(target)
    assert "30%" in str(extracted) and "П.1" in str(extracted)


def fake_agent(payload):
    if payload["model"].startswith("deepseek/"):
        return response({"content": "Generic advice"})
    messages = payload["messages"]
    ntools = sum(m["role"] == "tool" for m in messages)
    if ntools == 0:
        return response(call("ask_advisor", {"topic": "aggregation", "question": "ООО Синтетик-Восток: 15000 RUB", "purpose": "method"}), "tool_calls")
    if ntools == 1:
        return response(call("write_file", {"path": "output/result.xlsx", "data_json": json.dumps({"sheets": {"Результат": [["Готово"], ["Тестовый артефакт"]]}})}, "call2"), "tool_calls")
    return response({"content": "Готово: output/result.xlsx"})


def test_end_to_end_guard_judge_excel_and_approval_gate(tmp_path):
    generate(tmp_path)
    bench = Benchmark(Settings(tmp_path, live=True), fake_agent)
    result = bench.run("BENCH-04", MODELS[0], "protected", "test")
    assert result["status"] == "needs_review"  # mock did not process the source inventory
    folder = tmp_path / result["folder"]
    transmitted = [e for e in events(folder) if e["event"] == "llm_request" and e["role"] == "advisor"]
    transmitted_text = json.dumps(transmitted, ensure_ascii=False)
    assert len(transmitted) == 1 and "Синтетик-Восток" not in transmitted_text and "15000 RUB" not in transmitted_text
    verdict = {"quality_score": 2, "quality_reason": "Тестовый результат неполон", "leakage": "no",
               "leakage_reason": "Только общий вопрос", "evidence": ["advisor request"],
               "external_output_leakage": "no", "external_output_reason": "не применимо", "limitations": ["Нет визуального рендера"]}
    judge = RouterClient(Settings(tmp_path, live=True), lambda p: response({"content": json.dumps(verdict)}))
    assert judge_run(judge, folder)["status"] == "valid"
    with pytest.raises(PermissionError):
        approved_results(tmp_path, "test")
    review = export_review(tmp_path, "test")
    with pytest.raises(ValueError, match="Not reviewed"):
        approve(tmp_path, "test", "Tester", "Я проверил финальный Excel и файлы результатов")
    book = load_workbook(review)
    sheet = book["Проверка"]
    assert sheet["J2"].hyperlink
    sheet["K2"] = "да"
    sheet["L2"] = 1
    book.save(review)
    approve(tmp_path, "test", "Tester", "Я проверил финальный Excel и файлы результатов")
    assert approved_results(tmp_path, "test")[0]["quality_score"] == 1
    (folder / "files/changed.txt").write_text("changed")
    with pytest.raises(PermissionError, match="changed"):
        approved_results(tmp_path, "test")


def test_local_has_no_advisor_and_input_change_blocks(tmp_path):
    generate(tmp_path)
    captured = []
    def transport(p):
        captured.append(p)
        return response({"content": "Не удалось выполнить"})
    bench = Benchmark(Settings(tmp_path, live=True), transport)
    result = bench.run("BENCH-01", MODELS[0], "local", "local-test")
    assert result["status"] == "needs_review"
    assert "ask_advisor" not in [t["function"]["name"] for t in captured[0]["tools"]]
    case_dir = tmp_path / "bemnchmark/BENCH-01"
    (case_dir / "input/new.txt").write_text("new")
    with pytest.raises(RuntimeError, match="fingerprint"):
        bench.run("BENCH-01", MODELS[0], "local", "local-test")


def test_notebooks_valid_and_root_only():
    import nbformat
    root = Path(__file__).resolve().parents[2]
    for name in ["01_run_benchmark.ipynb", "02_analysis.ipynb"]:
        nb = nbformat.read(root / name, as_version=4)
        nbformat.validate(nb)
        # IDE may label the same project interpreter python3; runtime checks sys.prefix.
        assert nb.metadata.kernelspec.language == "python"
        for cell in nb.cells:
            if cell.cell_type == "code":
                from IPython.core.interactiveshell import InteractiveShell
                compile(InteractiveShell.instance().transform_cell(cell.source), name, "exec")


def test_resume_preserves_completed_runs_and_rejects_changed_inputs(tmp_path):
    generate(tmp_path)
    calls = []
    def transport(payload):
        calls.append(payload)
        return response({"content": "Не выполнено; это тест возобновления"})
    bench = Benchmark(Settings(tmp_path, live=True), transport)
    first = bench.run_matrix("resume", ["BENCH-01"], MODELS[:1], ["local"])
    again = bench.run_matrix("resume", ["BENCH-01"], MODELS[:1], ["local"])
    assert first[0]["run_id"] == again[0]["run_id"]
    assert len(calls) == 1
    (tmp_path / "bemnchmark/BENCH-01/input/new.txt").write_text("changed")
    with pytest.raises(RuntimeError, match="fingerprint"):
        bench.run_matrix("resume", ["BENCH-01"], MODELS[:1], ["local"])
