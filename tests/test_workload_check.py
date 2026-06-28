"""Tests for the supervised workload-check command."""

import argparse
import json
from unittest.mock import patch

from xdna_top import workload_check as wc


def _ns(**kw):
    base = dict(
        chat_url="http://localhost:8000/v1/chat/completions",
        model="test-model",
        models_url=None,
        prompt=None,
        timeout=5.0,
        npu_device=None,
        out=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


# --- pure delta logic --------------------------------------------------------
def test_delta_contexts_reports_only_movement():
    before = [
        {"pid": 1234, "ctx_id": 1, "submissions": 10, "completions": 10},
        {"pid": 5, "ctx_id": 0, "submissions": 3, "completions": 3},
    ]
    after = [
        {"pid": 1234, "ctx_id": 1, "submissions": 52, "completions": 50},
        {"pid": 5, "ctx_id": 0, "submissions": 3, "completions": 3},  # idle, no delta
    ]
    with patch("xdna_top.workload_check.resolve_process_name", return_value="llama-server"):
        deltas = wc._delta_contexts(before, after)
    assert len(deltas) == 1
    d = deltas[0]
    assert d["pid"] == 1234 and d["ctx_id"] == 1
    assert d["submission_delta"] == 42 and d["completion_delta"] == 40
    assert d["process_name"] == "llama-server"


def test_model_count_parses_openai_models():
    body = json.dumps({"data": [{"id": "a"}, {"id": "b"}]})
    assert wc._model_count(body) == 2
    assert wc._model_count("not json") is None
    assert wc._model_count(None) is None


# --- run_workload_check orchestration ----------------------------------------
@patch("xdna_top.workload_check._contexts", return_value=[])
@patch("xdna_top.workload_check._http_post_json")
def test_endpoint_failure(mock_post, mock_ctx):
    mock_post.return_value = {
        "ok": False,
        "status": None,
        "latency_s": None,
        "body": None,
        "error": "Connection refused",
    }
    result = wc.run_workload_check(
        models_url=None, chat_url="http://x/chat", model="m"
    )
    assert result["endpoint"]["chat"]["ok"] is False
    assert result["endpoint"]["chat"]["error"] == "Connection refused"
    assert result["npu"]["active_contexts"] == []
    assert result["measured"] == [
        "No NPU context counter movement observed during the request window."
    ]
    assert "causality" in result["caveat"]
    assert wc.workload_check_main(_ns(chat_url="http://x/chat", model="m")) == 1


@patch(
    "xdna_top.workload_check._contexts",
    return_value=[{"pid": 1234, "ctx_id": 1, "submissions": 10, "completions": 10}],
)
@patch("xdna_top.workload_check._http_post_json")
def test_success_no_npu_activity(mock_post, mock_ctx):
    mock_post.return_value = {
        "ok": True,
        "status": 200,
        "latency_s": 0.12,
        "body": json.dumps({"id": "chatcmpl-9", "usage": {"total_tokens": 7}, "choices": [{"finish_reason": "stop"}]}),
        "error": None,
    }
    result = wc.run_workload_check(models_url=None, chat_url="http://x/chat", model="m")
    assert result["endpoint"]["chat"]["ok"] is True
    assert result["endpoint"]["chat"]["id"] == "chatcmpl-9"
    assert result["endpoint"]["chat"]["usage"] == {"total_tokens": 7}
    assert result["npu"]["active_contexts"] == []
    assert result["npu"]["max_submission_delta"] == 0
    assert result["measured"][0].startswith("No NPU context counter movement")
    assert wc.workload_check_main(_ns()) == 0


@patch("xdna_top.workload_check.resolve_process_name", return_value=None)
@patch(
    "xdna_top.workload_check._contexts",
    side_effect=[
        [{"pid": 1234, "ctx_id": 1, "submissions": 10, "completions": 10}],
        [{"pid": 1234, "ctx_id": 1, "submissions": 52, "completions": 50}],
    ],
)
@patch("xdna_top.workload_check._http_post_json")
def test_success_with_observed_deltas(mock_post, mock_ctx, mock_name):
    mock_post.return_value = {
        "ok": True,
        "status": 200,
        "latency_s": 0.2,
        "body": json.dumps({"id": "chatcmpl-1", "choices": [{"finish_reason": "stop"}]}),
        "error": None,
    }
    result = wc.run_workload_check(models_url=None, chat_url="http://x/chat", model="m")
    assert result["endpoint"]["chat"]["finish_reason"] == "stop"
    active = result["npu"]["active_contexts"]
    assert len(active) == 1
    assert active[0]["submission_delta"] == 42
    assert result["npu"]["max_submission_delta"] == 42
    assert "submission_delta=42 during request window" in result["measured"][0]
    assert "PID 1234 context 1" in result["measured"][0]


@patch("xdna_top.workload_check._contexts", side_effect=[[], []])
@patch("xdna_top.workload_check._http_get")
@patch("xdna_top.workload_check._http_post_json")
def test_models_probe_included_when_url_given(mock_post, mock_get, mock_ctx):
    mock_post.return_value = {"ok": True, "status": 200, "latency_s": 0.1, "body": "{}", "error": None}
    mock_get.return_value = {
        "ok": True,
        "status": 200,
        "latency_s": 0.05,
        "body": json.dumps({"data": [{"id": "m1"}]}),
        "error": None,
    }
    result = wc.run_workload_check(
        models_url="http://x/models", chat_url="http://x/chat", model="m"
    )
    models = result["endpoint"]["models"]
    assert models["ok"] is True and models["model_count"] == 1


def test_out_file_written(tmp_path):
    out = tmp_path / "wc.json"
    with patch("xdna_top.workload_check._contexts", side_effect=[[], []]), patch(
        "xdna_top.workload_check._http_post_json",
        return_value={"ok": True, "status": 200, "latency_s": 0.1, "body": "{}", "error": None},
    ):
        rc = wc.workload_check_main(_ns(out=str(out)))
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["kind"] == "xdna-top.workload-check"
    assert "caveat" in data
