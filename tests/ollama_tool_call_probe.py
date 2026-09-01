from langchain_ollama import ChatOllama

from ai_ems import load_network
from ai_ems.agent.tools import create_agent_tools

network = load_network(
  "data/KPG193_ver2_0_pypowsybl.mat"
)

tools = create_agent_tools(network)

model = ChatOllama(
    model="qwen2:7b",
    base_url="http://host.docker.internal:11434",
    temperature=0,
)

model_with_tools = model.bind_tools(tools)

response = model_with_tools.invoke(
    "LINE-16-28 선로가 탈락하면 계통에 어떤 문제가 생기는지 분석해줘. "
    "LINE-16-22를 모니터링해."
)

print("=== Content ===")
print(response.content)

print("\n=== Tool Calls ===")
print(response.tool_calls)