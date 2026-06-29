"""Registration checks for project discussion and documentation tools."""

from __future__ import annotations

from pathlib import Path


def test_discuss_project_empty_topic_returns_help():
    from actions.discuss_project import discuss_project

    assert "would you like" in discuss_project({"topic": ""}).lower()


def test_search_docs_empty_query_returns_help():
    from actions.search_docs import search_docs

    assert "would you like" in search_docs({"query": ""}).lower()


def test_discussion_tools_are_registered():
    import hybrid.bootstrap as bootstrap

    handlers = bootstrap._build_handlers()
    assert callable(handlers.get("discuss_project"))
    assert callable(handlers.get("search_docs"))
    assert bootstrap._TOOL_META["discuss_project"]["agent"] == "research"
    assert bootstrap._TOOL_META["search_docs"]["category"] == "discussion"
    assert "discuss_project" in bootstrap._SLOW_TOOLS
    assert "search_docs" in bootstrap._SLOW_TOOLS


def test_removed_declarations_global_does_not_return():
    source = (
        Path(__file__).resolve().parent.parent / "hybrid" / "bootstrap.py"
    ).read_text(encoding="utf-8")
    assert "TOOL_DECLARATIONS" not in source
