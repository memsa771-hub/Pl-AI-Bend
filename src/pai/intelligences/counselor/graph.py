from __future__ import annotations

from langgraph.graph import END, StateGraph

from pai.intelligences.counselor.state import PAIState


def build_pai_graph(orchestrator) -> StateGraph:
    graph = StateGraph(PAIState)

    graph.add_node("load_student_context", orchestrator.node_load_student_context)
    graph.add_node("serve_turn", orchestrator.node_serve_turn)

    graph.set_entry_point("load_student_context")
    graph.add_edge("load_student_context", "serve_turn")
    graph.add_edge("serve_turn", END)
    return graph
