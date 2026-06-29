"""Tests for app.features.qa.graph — build_qa_graph compilation + structure."""

from __future__ import annotations


def test_graph_compiles() -> None:
    from app.features.qa.graph import build_qa_graph

    g = build_qa_graph()
    compiled = g.compile()
    assert compiled is not None


def test_graph_has_5_nodes() -> None:
    from app.features.qa.graph import build_qa_graph

    g = build_qa_graph()
    compiled = g.compile()
    node_names = set(compiled.nodes.keys())
    expected = {
        "awaiting_identification",
        "awaiting_policy_choice",
        "answering_qa",
        "escalating",
        "closed",
    }
    assert expected <= node_names, f"Missing nodes: {expected - node_names}"


def test_graph_compiles_with_entry_in_nodes() -> None:
    from app.features.qa.graph import build_qa_graph

    g = build_qa_graph()
    compiled = g.compile()
    # Entry node should be present in the graph
    assert "awaiting_identification" in compiled.nodes
