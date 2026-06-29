from actions import project_starter as starter


def test_python_project_scaffold_and_research(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "actions.web_search.web_search",
        lambda parameters: "Comparable project: Example — https://example.com",
    )

    result = starter.project_starter({
        "name": "Friday Core",
        "description": "A proactive desktop assistant",
        "stack": "Python",
        "parent": str(tmp_path),
    })
    project = tmp_path / "friday-core"

    assert result.startswith("Created Friday Core")
    assert (project / "README.md").exists()
    assert (project / "pyproject.toml").exists()
    assert (project / "src" / "friday_core" / "__init__.py").exists()
    assert "Comparable project" in (project / "RESEARCH.md").read_text(encoding="utf-8")


def test_existing_project_is_never_overwritten(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("user data", encoding="utf-8")

    result = starter.project_starter({"name": "existing", "parent": str(tmp_path)})

    assert result.startswith("NEEDS_USER:")
    assert marker.read_text(encoding="utf-8") == "user data"


def test_missing_project_name_requests_user_input(tmp_path):
    assert starter.project_starter({"parent": str(tmp_path)}).startswith("NEEDS_USER:")
