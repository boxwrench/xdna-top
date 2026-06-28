"""Supervised ``workload-check``: probe an OpenAI-compatible endpoint and measure
NPU context counter movement across the request window.

Claims precision (the house rule): this reports *measured* per-context
submission/completion deltas observed between a "before" and "after" reading that
bracket a supervised request. It never claims the request *caused* the NPU work —
a concurrent workload can move the same counters. PID-owned context deltas are the
evidence; causality is not asserted, and that caveat travels in the output.

No new dependency: endpoint probing uses the standard-library ``urllib``.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any

from xdna_top.gauge import parse_xrt_smi, resolve_process_name, run_xrt_smi

KIND = "xdna-top.workload-check"
DEFAULT_PROMPT = "Reply with the single word: ok."
DEFAULT_TIMEOUT_S = 30.0


def _http_get(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (user-supplied URL by design)
            body = resp.read().decode("utf-8", "replace")
            return {
                "ok": True,
                "status": getattr(resp, "status", None),
                "latency_s": round(time.time() - start, 3),
                "body": body,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "latency_s": round(time.time() - start, 3), "body": None, "error": f"http_{exc.code}"}
    except Exception as exc:  # URLError, timeout, connection refused, ...
        return {"ok": False, "status": None, "latency_s": None, "body": None, "error": str(exc)}


def _http_post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", "replace")
            return {
                "ok": True,
                "status": getattr(resp, "status", None),
                "latency_s": round(time.time() - start, 3),
                "body": body,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "latency_s": round(time.time() - start, 3), "body": None, "error": f"http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "status": None, "latency_s": None, "body": None, "error": str(exc)}


def _contexts(npu_device: str | None) -> list[dict[str, Any]]:
    """Best-effort current NPU hardware contexts via xrt-smi; [] when unavailable."""
    out = run_xrt_smi(device=npu_device)
    if not out:
        return []
    try:
        return parse_xrt_smi(out)
    except Exception:
        return []


def _model_count(models_body: str | None) -> int | None:
    """Count models in an OpenAI ``/v1/models`` response, if parseable."""
    if not models_body:
        return None
    try:
        data = json.loads(models_body)
    except Exception:
        return None
    items = data.get("data") if isinstance(data, dict) else None
    return len(items) if isinstance(items, list) else None


def _chat_summary(chat: dict[str, Any]) -> dict[str, Any]:
    """Extract a small, non-verbatim summary of the chat response."""
    summary: dict[str, Any] = {
        "ok": chat["ok"],
        "status": chat["status"],
        "latency_s": chat["latency_s"],
        "error": chat["error"],
        "id": None,
        "usage": None,
        "finish_reason": None,
    }
    if chat.get("body"):
        try:
            data = json.loads(chat["body"])
            summary["id"] = data.get("id")
            summary["usage"] = data.get("usage")
            choices = data.get("choices") or []
            if choices:
                summary["finish_reason"] = choices[0].get("finish_reason")
        except Exception:
            pass
    return summary


def _delta_contexts(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-context submission/completion deltas for contexts that moved."""
    before_map = {(c.get("pid"), c.get("ctx_id")): c for c in before}
    deltas = []
    for c in after:
        key = (c.get("pid"), c.get("ctx_id"))
        prev = before_map.get(key, {})
        sub_delta = (c.get("submissions") or 0) - (prev.get("submissions") or 0)
        comp_delta = (c.get("completions") or 0) - (prev.get("completions") or 0)
        if sub_delta > 0 or comp_delta > 0:
            deltas.append(
                {
                    "pid": c.get("pid"),
                    "process_name": resolve_process_name(c.get("pid")) if c.get("pid") else None,
                    "ctx_id": c.get("ctx_id"),
                    "submission_delta": sub_delta,
                    "completion_delta": comp_delta,
                }
            )
    deltas.sort(key=lambda d: d["submission_delta"], reverse=True)
    return deltas


def run_workload_check(
    *,
    models_url: str | None,
    chat_url: str,
    model: str,
    prompt: str = DEFAULT_PROMPT,
    timeout: float = DEFAULT_TIMEOUT_S,
    npu_device: str | None = None,
) -> dict[str, Any]:
    """Run the supervised check and return a machine-readable evidence dict."""
    models = _http_get(models_url, timeout) if models_url else None

    before = _contexts(npu_device)
    started = time.time()
    chat = _http_post_json(
        chat_url,
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "stream": False,
        },
        timeout,
    )
    ended = time.time()
    after = _contexts(npu_device)

    active = _delta_contexts(before, after)
    max_sub = max((d["submission_delta"] for d in active), default=0)

    measured: list[str] = []
    for d in active:
        name = f" ({d['process_name']})" if d.get("process_name") else ""
        measured.append(
            f"Observed PID {d['pid']}{name} context {d['ctx_id']} "
            f"submission_delta={d['submission_delta']} during request window"
        )
    if not measured:
        measured.append(
            "No NPU context counter movement observed during the request window."
        )

    return {
        "kind": KIND,
        "endpoint": {
            "models_url": models_url,
            "chat_url": chat_url,
            "model": model,
            "models": (
                None
                if models is None
                else {
                    "ok": models["ok"],
                    "status": models["status"],
                    "latency_s": models["latency_s"],
                    "error": models["error"],
                    "model_count": _model_count(models.get("body")),
                }
            ),
            "chat": _chat_summary(chat),
        },
        "npu": {
            "contexts_present_before": len(before),
            "contexts_present_after": len(after),
            "active_contexts": active,
            "max_submission_delta": max_sub,
        },
        "window": {
            "started_at": started,
            "ended_at": ended,
            "duration_s": round(ended - started, 3),
        },
        "measured": measured,
        "caveat": (
            "Counter deltas are measured evidence, not proof of causality; a "
            "concurrent workload can move the same context counters."
        ),
    }


def workload_check_main(args: argparse.Namespace) -> int:
    """CLI entry: run the check, write JSON, print measured-language summary.

    Exit code is ``0`` when the chat endpoint responded successfully, ``1``
    otherwise. NPU activity presence never changes the exit code — that would be a
    causality claim the counters do not support.
    """
    result = run_workload_check(
        models_url=getattr(args, "models_url", None),
        chat_url=args.chat_url,
        model=args.model,
        prompt=getattr(args, "prompt", None) or DEFAULT_PROMPT,
        timeout=getattr(args, "timeout", DEFAULT_TIMEOUT_S),
        npu_device=getattr(args, "npu_device", None),
    )

    out = getattr(args, "out", None)
    payload = json.dumps(result, indent=2) + "\n"
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print(payload, end="")

    chat = result["endpoint"]["chat"]
    status = "ok" if chat["ok"] else f"FAILED ({chat['error']})"
    print(f"endpoint chat: {status}", flush=True)
    for line in result["measured"]:
        print(line, flush=True)
    print(result["caveat"], flush=True)

    return 0 if chat["ok"] else 1
