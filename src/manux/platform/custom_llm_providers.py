"""LLM providers for the "connect gestures in words" helper (src/llm_binding.py).

One call shape for every provider: a system prompt, the user's text and a JSON schema -> a dict.

- Claude: the official `anthropic` SDK (optional extra `llm`: `uv sync --locked --extra qt --extra train --extra llm`),
  structured outputs (`output_config.format`) so the answer always matches the schema, server-side refusal fallback.
- OpenAI, Gemini, OpenRouter, Ollama: their OpenAI-compatible Chat Completions endpoint over plain HTTP (standard
  library only), `response_format` JSON; the answer is parsed and checked by the caller like any untrusted text.

Only the user's sentence and the gesture/command catalogue are sent: no camera image, no joints, no window titles.
Keys come from the environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY) or from the key
saved in the app (src/secret_store.py). Ollama runs on this PC and needs no key.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    key_env: str = ""             # "" = no key (Ollama)
    default_model: str = ""       # "" = the user picks one ("모델 목록 불러오기")
    note: str = ""


PROVIDERS: Dict[str, Provider] = {p.id: p for p in (
    Provider("claude", "Claude (Anthropic)", "", "ANTHROPIC_API_KEY", "claude-opus-5-5",
             "Anthropic 공식 SDK. 키: console.anthropic.com"),
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY", "",
             "키: platform.openai.com"),
    Provider("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "",
             "키: aistudio.google.com"),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "",
             "여러 회사 모델을 한 키로. 모델 이름 예: 회사/모델"),
    Provider("ollama", "Ollama (이 PC)", "http://localhost:11434/v1", "", "",
             "이 PC에서 실행. 키 필요 없음, 인터넷으로 나가지 않음"),
)}


class LlmError(RuntimeError):
    """A failure the screen can show as it is (Korean, no key in it)."""


@dataclass
class LlmConfig:
    provider: str = "claude"
    model: str = ""
    base_url: str = ""            # "" = the provider's default
    api_key: str = ""             # "" = from the environment
    timeout: float = 60.0
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def spec(self) -> Provider:
        if self.provider not in PROVIDERS:
            raise LlmError(f"알 수 없는 연결 대상: {self.provider}")
        return PROVIDERS[self.provider]

    def key(self) -> str:
        spec = self.spec
        return (self.api_key or (os.environ.get(spec.key_env, "") if spec.key_env else "")).strip()

    def url(self) -> str:
        return (self.base_url or self.spec.base_url).rstrip("/")

    def model_name(self) -> str:
        return (self.model or self.spec.default_model).strip()


# ---------------------------------------------------------------------------- the call
def complete_json(cfg: LlmConfig, system: str, user: str, schema: Dict[str, Any], max_tokens: int = 4000) -> Dict[str, Any]:
    """Ask the provider; the answer as a dict (shape checked by the caller). Raises LlmError with a readable message."""
    if not cfg.model_name():
        raise LlmError("모델 이름을 정해 주세요 ([모델 목록 불러오기] 또는 직접 입력).")
    if cfg.spec.key_env and not cfg.key():
        raise LlmError(f"{cfg.spec.label} 키가 없어요. 키를 저장하거나 환경 변수 {cfg.spec.key_env}를 설정해 주세요.")
    if cfg.provider == "claude":
        return _claude(cfg, system, user, schema, max_tokens)
    return _openai_compatible(cfg, system, user, schema, max_tokens)


def list_models(cfg: LlmConfig) -> List[str]:
    """Model names the provider offers to this key (Ollama: the models pulled on this PC)."""
    if cfg.spec.key_env and not cfg.key():
        raise LlmError(f"{cfg.spec.label} 키가 없어요.")
    if cfg.provider == "claude":
        anthropic = _anthropic()
        client = anthropic.Anthropic(api_key=cfg.key(), timeout=cfg.timeout)
        try:
            return [m.id for m in client.models.list()]
        except anthropic.APIError as exc:
            raise LlmError(_claude_error(anthropic, exc)) from exc
    data = _http_json("GET", cfg.url() + "/models", cfg, None)
    names = [str(m.get("id", "")) for m in (data.get("data") or []) if isinstance(m, dict)]
    if cfg.provider == "gemini":
        names = [n.split("/", 1)[1] if n.startswith("models/") else n for n in names]
    return sorted(n for n in names if n)


# ---------------------------------------------------------------------------- Claude (official SDK)
def _anthropic():
    try:
        import anthropic
    except ImportError as exc:
        raise LlmError("Claude 연결 라이브러리가 없어요. 저장소 폴더에서 "
                       "`uv sync --locked --extra qt --extra train --extra llm` 을 실행해 주세요.") from exc
    return anthropic


def _claude_error(anthropic, exc) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude 키가 맞지 않아요."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "이 키로는 이 모델을 쓸 수 없어요."
    if isinstance(exc, anthropic.NotFoundError):
        return "모델 이름이 맞지 않아요."
    if isinstance(exc, anthropic.RateLimitError):
        return "요청이 너무 많아요. 잠시 뒤 다시 해 주세요."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Claude에 연결하지 못했어요 (인터넷 확인)."
    if isinstance(exc, anthropic.APIStatusError):
        return f"Claude 오류 ({exc.status_code}): {getattr(exc, 'message', '')}"[:300]
    return f"Claude 오류: {exc}"[:300]


# models that take the server-side refusal fallback (`fallbacks: "default"`)
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1")


def _claude(cfg: LlmConfig, system: str, user: str, schema: Dict[str, Any], max_tokens: int) -> Dict[str, Any]:
    anthropic = _anthropic()
    client = anthropic.Anthropic(api_key=cfg.key(), timeout=cfg.timeout)
    model = cfg.model_name()
    args = dict(model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}})
    try:
        if model in _FALLBACK_MODELS:
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **args)
        else:
            response = client.messages.create(**args)
    except anthropic.APIError as exc:
        raise LlmError(_claude_error(anthropic, exc)) from exc
    if response.stop_reason == "refusal":
        raise LlmError("Claude가 이 요청에 답하지 않았어요. 문장을 바꿔 다시 해 주세요.")
    if response.stop_reason == "max_tokens":
        raise LlmError("답이 너무 길어 잘렸어요. 한 번에 부탁하는 연결 수를 줄여 주세요.")
    text = "".join(b.text for b in response.content if b.type == "text")
    return parse_json(text)


# ---------------------------------------------------------------------------- OpenAI-compatible (OpenAI, Gemini, OpenRouter, Ollama)
def _http_json(method: str, url: str, cfg: LlmConfig, body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.key():
        headers["Authorization"] = "Bearer " + cfg.key()
    if cfg.provider == "openrouter":
        headers["X-Title"] = "MANUX"           # shown in the user's OpenRouter dashboard; nothing personal
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout) as response:  # noqa: S310 (fixed https/localhost URLs)
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
            detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
        except Exception:
            pass
        raise _HttpStatus(exc.code, _status_text(cfg, exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        where = "Ollama가 실행 중인지" if cfg.provider == "ollama" else "인터넷 연결을"
        raise LlmError(f"{cfg.spec.label}에 연결하지 못했어요. {where} 확인해 주세요.") from exc
    except (TimeoutError, OSError) as exc:
        raise LlmError(f"{cfg.spec.label} 응답이 너무 늦어요 ({cfg.timeout:.0f}초).") from exc
    except json.JSONDecodeError as exc:
        raise LlmError(f"{cfg.spec.label}의 응답을 읽지 못했어요.") from exc


class _HttpStatus(LlmError):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code


def _status_text(cfg: LlmConfig, code: int, detail: str) -> str:
    name = cfg.spec.label
    if code in (401, 403):
        return f"{name} 키가 맞지 않거나 권한이 없어요."
    if code == 404:
        return f"{name}: 모델 이름이나 주소가 맞지 않아요."
    if code == 429:
        return f"{name}: 요청이 너무 많거나 사용 한도를 넘었어요."
    return f"{name} 오류 ({code}) {detail}"[:300]


def _openai_compatible(cfg: LlmConfig, system: str, user: str, schema: Dict[str, Any], max_tokens: int) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": cfg.model_name(),
        "messages": [{"role": "system", "content": system + "\n\nAnswer with one JSON object only, matching this JSON "
                      "schema:\n" + json.dumps(schema, ensure_ascii=False)},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if cfg.provider == "openai":
        body["max_completion_tokens"] = body.pop("max_tokens")
        if cfg.model_name() == "gpt-6-luna":
            body["reasoning_effort"] = "none"
    try:
        data = _http_json("POST", cfg.url() + "/chat/completions", cfg, body)
    except _HttpStatus as exc:
        if exc.code != 400:
            raise
        body.pop("response_format")          # some models / servers refuse JSON mode: ask in words only
        data = _http_json("POST", cfg.url() + "/chat/completions", cfg, body)
    try:
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise LlmError(f"{cfg.spec.label}의 응답 형식이 예상과 달라요.") from exc
    if choice.get("finish_reason") == "length":
        raise LlmError("답이 너무 길어 잘렸어요. 한 번에 부탁하는 연결 수를 줄여 주세요.")
    return parse_json(text)


def parse_json(text: str) -> Dict[str, Any]:
    """The JSON object in a model's answer (```json fences and text around it are tolerated)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError("모델의 답을 JSON으로 읽지 못했어요. 다시 해 보거나 다른 모델을 골라 주세요.") from exc
    if not isinstance(value, dict):
        raise LlmError("모델의 답이 예상한 형식이 아니에요.")
    return value
