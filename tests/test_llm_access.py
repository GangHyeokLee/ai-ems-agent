import asyncio
import json

import httpx
import pytest
from langchain_ollama import ChatOllama

from ai_ems.config import get_llm_client_kwargs

ID_ENV = "AI_EMS_CF_ACCESS_CLIENT_ID"
SECRET_ENV = "AI_EMS_CF_ACCESS_CLIENT_SECRET"


@pytest.fixture(autouse=True)
def clear_access_env(monkeypatch):
    monkeypatch.delenv(ID_ENV, raising=False)
    monkeypatch.delenv(SECRET_ENV, raising=False)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "https://ollama.example.com"])
def test_unset_credentials_preserve_local_behavior(url):
    assert get_llm_client_kwargs(url) == {}


def test_empty_credentials_preserve_local_behavior(monkeypatch):
    monkeypatch.setenv(ID_ENV, " ")
    monkeypatch.setenv(SECRET_ENV, "")
    assert get_llm_client_kwargs("http://127.0.0.1:11434") == {}


@pytest.mark.parametrize("name", [ID_ENV, SECRET_ENV])
def test_partial_credentials_fail_without_revealing_value(monkeypatch, name):
    monkeypatch.setenv(name, "dummy-sensitive-value")
    with pytest.raises(ValueError) as error:
        get_llm_client_kwargs("https://ollama.example.com")
    assert "dummy-sensitive-value" not in str(error.value)


def test_credentials_require_https(monkeypatch):
    monkeypatch.setenv(ID_ENV, "dummy-id")
    monkeypatch.setenv(SECRET_ENV, "dummy-secret")
    with pytest.raises(ValueError, match="HTTPS"):
        get_llm_client_kwargs("http://127.0.0.1:11434")


@pytest.mark.parametrize("authenticated", [False, True])
def test_sync_and_async_requests(monkeypatch, authenticated):
    if authenticated:
        monkeypatch.setenv(ID_ENV, "dummy-id")
        monkeypatch.setenv(SECRET_ENV, "dummy-secret")
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload["model"] == "test-model"
        assert payload["tools"][0]["function"]["name"] == "test_tool"
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=json.dumps({
                "model": "test-model",
                "message": {"role": "assistant", "content": "OK"},
                "done": True,
            }) + "\n",
        )

    kwargs = get_llm_client_kwargs("https://ollama.example.com")
    model = ChatOllama(
        model="test-model",
        base_url="https://ollama.example.com",
        client_kwargs={**kwargs, "transport": httpx.MockTransport(handle)},
    )
    bound = model.bind_tools([{
        "type": "function",
        "function": {
            "name": "test_tool",
            "description": "Test tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }])
    try:
        assert bound.invoke("hello").content == "OK"
        assert asyncio.run(bound.ainvoke("hello")).content == "OK"
        assert len(requests) == 2
        for request in requests:
            assert request.headers.get("CF-Access-Client-Id") == (
                "dummy-id" if authenticated else None
            )
            assert request.headers.get("CF-Access-Client-Secret") == (
                "dummy-secret" if authenticated else None
            )
    finally:
        model._client._client.close()
        asyncio.run(model._async_client._client.aclose())


@pytest.mark.parametrize("authenticated", [False, True])
def test_agent_graph_passes_optional_client_kwargs(monkeypatch, authenticated):
    from ai_ems.agent import graph

    if authenticated:
        monkeypatch.setenv(ID_ENV, "dummy-id")
        monkeypatch.setenv(SECRET_ENV, "dummy-secret")
    monkeypatch.setattr(graph, "create_agent_tools", lambda network: [])

    class Captured(Exception):
        pass

    def capture(**kwargs):
        if authenticated:
            assert kwargs["client_kwargs"]["headers"] == {
                "CF-Access-Client-Id": "dummy-id",
                "CF-Access-Client-Secret": "dummy-secret",
            }
        else:
            assert "client_kwargs" not in kwargs
        assert kwargs["base_url"] == "https://ollama.example.com"
        raise Captured

    monkeypatch.setattr(graph, "ChatOllama", capture)
    with pytest.raises(Captured):
        graph.create_agent_graph(None, base_url="https://ollama.example.com")
