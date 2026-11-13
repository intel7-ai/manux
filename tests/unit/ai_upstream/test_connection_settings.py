"""Connection adapters use synthetic keys and never make API calls."""
import pytest


@pytest.mark.parametrize("provider,model,token_field,reasoning", [
    ("openai", "gpt-6-luna", "max_completion_tokens", "none"),
    ("openai", "gpt-4.1-mini", "max_completion_tokens", None),
    ("openrouter", "some/model", "max_tokens", None),
    ("ollama", "local-model", "max_tokens", None),
])
def test_token_limits_match_each_provider(monkeypatch, provider, model, token_field, reasoning):
    from manux.platform import custom_llm_providers as providers
    sent = []

    def reply(method, url, config, body):
        sent.append(body)
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}

    monkeypatch.setattr(providers, "_http_json", reply)
    result = providers.complete_json(providers.LlmConfig(provider=provider, model=model, api_key="synthetic-only"),
                                     "Return JSON", "Check", {"type": "object"}, max_tokens=512)
    assert result == {"ok": True}
    assert sent[0][token_field] == 512
    assert ("max_tokens" in sent[0]) != ("max_completion_tokens" in sent[0])
    assert sent[0].get("reasoning_effort") == reasoning


def test_linked_key_survives_restart_and_unlink_keeps_approved_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from manux.ui.ai_customization import AiCustomizationBridge

    application = QApplication.instance() or QApplication([])
    owner = QObject()
    env_file = tmp_path / ".env.local"
    contents = 'OPENAI_API_KEY="synthetic-only"\n'
    env_file.write_text(contents, encoding="utf-8")
    bridge = AiCustomizationBridge(owner)
    bridge.setLlmProvider("openai")
    bridge.setLlmModel("gpt-6-luna")
    bridge.preferences.setValue("llmEnvFile/openai", str(env_file))
    bridge.preferences.sync()
    bridge.close()
    restarted = AiCustomizationBridge(owner)
    try:
        assert restarted._llm_config().key() == "synthetic-only"
        assert restarted.llmSettings["hasKey"]
        assert restarted.llmSettings["keySource"] == "연결한 키 파일 사용"
        assert not restarted.preferences.contains("llmKey/openai")
        restarted.clearLlmKey()
        assert not restarted.llmSettings["hasKey"]
        assert env_file.read_text(encoding="utf-8") == contents
    finally:
        restarted.close()
