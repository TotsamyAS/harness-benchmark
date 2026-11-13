from pathlib import Path
import pytest
from scripts.benchlib.core import Settings, MODELS
from scripts.benchlib.inference import validate_request, validate_response

ROOT = Path(__file__).resolve().parents[2]


def test_default_settings_have_no_call_or_output_token_cap():
    settings = Settings(ROOT)
    assert settings.max_calls is None and settings.max_tokens is None
    validate_request({"model": MODELS[0], "messages": [{"role": "user", "content": "test"}]}, ROOT)


def test_approved_models_accept_current_tool_parameters():
    from scripts.benchlib.tools import TOOLS
    for model in MODELS:
        assert validate_request({"model": model, "messages": [{"role": "user", "content": "test"}],
                                 "tools": TOOLS, "max_tokens": 8000}, ROOT)["catalog_verified"]


def test_context_and_unsupported_parameter_blocked_before_send():
    payload = {"model": MODELS[1], "messages": [{"role": "user", "content": "x"*140000}], "max_tokens": 8000}
    with pytest.raises(ValueError, match="context"):
        validate_request(payload, ROOT)
    payload["messages"][0]["content"] = "test"
    payload["not_a_parameter"] = True
    with pytest.raises(ValueError, match="Parameter"):
        validate_request(payload, ROOT)


def test_broken_tool_history_rejected():
    payload = {"model": MODELS[0], "max_tokens": 10,
               "messages": [{"role": "tool", "tool_call_id": "orphan", "content": "result"}]}
    with pytest.raises(ValueError, match="Orphan"):
        validate_request(payload, ROOT)


def test_truncated_tool_reply_and_empty_reasoning_reply_rejected():
    with pytest.raises(ValueError, match="Incomplete"):
        validate_response({"choices": [{"finish_reason": "length", "message": {"tool_calls": []}}]})
    with pytest.raises(ValueError, match="Empty"):
        validate_response({"choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning": "thinking"}}]})


def test_settings_cannot_override_target_model_or_tools(tmp_path):
    with pytest.raises(ValueError, match="override"):
        Settings(tmp_path, model_parameters={MODELS[0]: {"model": "other"}})
