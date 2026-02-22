"""utils/ollama_client.py — Stdlib-only Ollama HTTP client.

No pip dependencies. Uses urllib only.

Environment variables (read at import time):
    OLLAMA_HOST   : host:port  (default 127.0.0.1:11434)
    OLLAMA_MODEL  : model tag  (default qwen2.5:7b-instruct-q4_K_M)
    OLLAMA_TIMEOUT: seconds    (default 120)

Public API
----------
    generate(prompt, model=None, temperature=0, num_ctx=1536, num_predict=512) -> str
        Call /api/generate and return response text.
        Retries up to 2 times on transient failure.

    is_available() -> bool
        Quick liveness check against /api/tags.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

_DEFAULT_HOST    = "127.0.0.1:11434"
_DEFAULT_MODEL   = "qwen2.5:7b-instruct-q4_K_M"
_DEFAULT_TIMEOUT = 120

OLLAMA_HOST    = os.environ.get("OLLAMA_HOST",    _DEFAULT_HOST).strip()
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL",   _DEFAULT_MODEL).strip()
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", str(_DEFAULT_TIMEOUT)))

# Normalise host (strip protocol if user pasted it)
if OLLAMA_HOST.startswith(("http://", "https://")):
    OLLAMA_HOST = OLLAMA_HOST.split("//", 1)[1]

BASE_URL = f"http://{OLLAMA_HOST}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _post_json(path: str, payload: dict, timeout: int) -> dict:
    """POST JSON to Ollama and return parsed response dict."""
    url  = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw)


def _get_json(path: str, timeout: int = 10) -> dict:
    """GET JSON from Ollama."""
    url = f"{BASE_URL}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Return True if Ollama daemon is reachable."""
    try:
        _get_json("/api/tags", timeout=5)
        return True
    except Exception:
        return False


def list_models() -> list[str]:
    """Return list of model names currently available in Ollama."""
    try:
        data = _get_json("/api/tags", timeout=10)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def generate(
    prompt: str,
    model: str | None = None,
    temperature: float = 0,
    top_p: float = 0.9,
    num_ctx: int = 1536,
    num_predict: int = 512,
    timeout: int | None = None,
    max_retries: int = 2,
) -> str:
    """Call /api/generate and return response text (non-streaming).

    Parameters
    ----------
    prompt       : Full prompt string.
    model        : Model tag; defaults to OLLAMA_MODEL env var.
    temperature  : 0 = deterministic.
    top_p        : Nucleus sampling.
    num_ctx      : Context window tokens.
    num_predict  : Max tokens to generate.
    timeout      : HTTP timeout in seconds; defaults to OLLAMA_TIMEOUT.
    max_retries  : Number of additional attempts on transient failure.

    Returns
    -------
    str — The 'response' field from Ollama, stripped of leading/trailing whitespace.
    Raises RuntimeError if all retries fail.
    """
    _model   = (model or OLLAMA_MODEL).strip()
    _timeout = timeout if timeout is not None else OLLAMA_TIMEOUT

    payload: dict[str, Any] = {
        "model":  _model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p":       top_p,
            "num_ctx":     num_ctx,
            "num_predict": num_predict,
        },
    }

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = _post_json("/api/generate", payload, _timeout)
            text = resp.get("response", "").strip()
            return text
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_err = exc
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 1s, 2s back-off
        except json.JSONDecodeError as exc:
            last_err = exc
            break  # malformed JSON — no point retrying

    raise RuntimeError(
        f"ollama_client.generate failed after {max_retries + 1} attempts "
        f"(model={_model!r}, host={BASE_URL!r}): {last_err}"
    )
