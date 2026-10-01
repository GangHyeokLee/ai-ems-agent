"""Manual live probe using WSL packages and Windows loopback HTTP transport.

The relay only bridges the NAT localhost boundary; every HTTP request reaches
Windows Caddy at 127.0.0.1:18080. It is not installed as a network service.
Read test secrets from stdin; never persist or print them.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))
settings = json.load(sys.stdin)
os.environ["AI_EMS_LLM_API_KEY"] = settings["token"]
os.environ.pop("AI_EMS_CF_ACCESS_CLIENT_ID", None)
os.environ.pop("AI_EMS_CF_ACCESS_CLIENT_SECRET", None)

import httpx
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_ollama import ChatOllama
from ai_ems import load_network
from ai_ems.agent.tools import create_agent_tools
from ai_ems.config import CASE_FILE, LLM_BASE_URL, get_llm_client_kwargs

paths = []


def handle(request):
    paths.append(request.url.path)
    wire = {
        "method": request.method,
        "path": request.url.path,
        "headers": dict(request.headers),
        "body": base64.b64encode(request.content).decode(),
    }
    result = subprocess.run(
        [settings["python"], settings["relay"]],
        input=json.dumps(wire), text=True, capture_output=True, check=True,
    )
    data = json.loads(result.stdout)
    return httpx.Response(data["status"], headers=data["headers"],
                          content=base64.b64decode(data["body"]))


kwargs = get_llm_client_kwargs("http://127.0.0.1:18080")
model = ChatOllama(
    model="qwen3.5:9b", base_url="http://127.0.0.1:18080",
    reasoning=False, temperature=0, num_predict=256,
    client_kwargs={**kwargs, "transport": httpx.MockTransport(handle)},
)
try:
    assert model.invoke("Reply with only OK.").content.strip() == "OK"
    assert asyncio.run(model.ainvoke("Reply with only OK.")).content.strip() == "OK"
    print("PASS AI-EMS auth helper + live sync/async ChatOllama via Windows Caddy", flush=True)

    network = load_network(CASE_FILE)
    tool = next(t for t in create_agent_tools(network) if t.name == "network_summary")
    bound = model.bind_tools([tool])
    messages = [HumanMessage(content="Call network_summary to get the grid summary. Do not guess.")]
    answer = bound.invoke(messages)
    assert answer.tool_calls and answer.tool_calls[0]["name"] == tool.name
    call = answer.tool_calls[0]
    summary = tool.invoke(call["args"])
    assert summary
    followup = bound.invoke(messages + [
        answer,
        ToolMessage(content=json.dumps(summary), tool_call_id=call["id"]),
    ])
    assert followup.content
    assert all(path == "/api/chat" for path in paths)
    print("PASS real AI-EMS network_summary tool call/execution/follow-up via proxy", flush=True)
finally:
    model._client._client.close()
    asyncio.run(model._async_client._client.aclose())

os.environ.pop("AI_EMS_LLM_API_KEY", None)
assert get_llm_client_kwargs(LLM_BASE_URL) == {}
local = ChatOllama(model="qwen3.5:9b", base_url=LLM_BASE_URL,
                   reasoning=False, num_predict=16, temperature=0)
try:
    assert local.invoke("Reply with only OK.").content.strip() == "OK"
    print("PASS existing unauthenticated local Ollama route", flush=True)
finally:
    local._client._client.close()
    asyncio.run(local._async_client._client.aclose())
