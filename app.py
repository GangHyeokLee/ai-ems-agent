import sys

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from ai_ems import load_network
from ai_ems.agent.graph import (
    SYSTEM_PROMPT,
    create_agent_graph,
)

CASE_FILE = "data/KPG193_ver2_0_pypowsybl.mat"


def main():
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(
            encoding="utf-8",
            errors="strict",
        )

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(
            encoding="utf-8",
            errors="strict",
        )
    
    network = load_network(CASE_FILE)
    graph = create_agent_graph(network)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
    ]

    print("AI-EMS Agent")
    print("Type 'exit' to quit.\n")

    while True:
        user_input = input("You> ").strip()

        if user_input.lower() in {"exit", "quit"}:
            print("Exiting...")
            break

        if not user_input:
            continue

        messages.append(HumanMessage(content=user_input))

        result = graph.invoke(
            {
                "messages": messages,
            }
        )

        messages = result["messages"]

        final_message = messages[-1]

        print()
        print("Agent>")
        print(final_message.content)
        print()

if __name__ == "__main__":
    main()
