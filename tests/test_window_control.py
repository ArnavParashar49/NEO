from actions import window_control as wc


def test_protects_neo_process_and_window():
    assert wc._is_protected(wc.WindowTarget(1, wc.os.getpid(), "python", "Anything"))
    assert wc._is_protected(wc.WindowTarget(2, 99999, "neo", "Assistant"))
    assert wc._is_protected(wc.WindowTarget(3, 99999, "other", "NEO"))


def test_target_selection_prefers_exact_process_and_can_filter_browsers():
    targets = [
        wc.WindowTarget(1, 10, "notepad", "Notes"),
        wc.WindowTarget(2, 11, "chrome", "Documentation - Google Chrome"),
    ]

    assert wc._match_target(targets, app="chrome").handle == 2
    assert wc._match_target(targets, browsers_only=True).handle == 2
    assert wc._match_target(targets, app="missing") is None


def test_close_app_requires_an_explicit_target():
    assert wc.window_control({"action": "close_app"}).startswith("FAILED:")


def test_windows_dispatches_each_close_intent(monkeypatch):
    monkeypatch.setattr(wc, "_OS", "Windows")
    monkeypatch.setattr(wc, "_windows_close_tab", lambda app, target="": f"tab:{app}:{target}")
    monkeypatch.setattr(
        wc, "_windows_close_window",
        lambda app, all_windows=False: f"window:{app}:{all_windows}",
    )

    assert wc.window_control({"action": "close_tab", "app": "Chrome"}) == "tab:Chrome:"
    assert wc.window_control({"action": "close_tab", "app": "Chrome", "target": "YouTube"}) == "tab:Chrome:YouTube"
    assert wc.window_control({"action": "close_tab", "app": "YouTube"}) == "tab::YouTube"
    assert wc.window_control({"action": "close_window", "app": "Notepad"}) == "window:Notepad:False"
    assert wc.window_control({"action": "close_app", "app": "Spotify"}) == "window:Spotify:True"


def test_registry_routes_closing_to_window_control_only():
    from hybrid.bootstrap import get_orchestrator

    registry = get_orchestrator().registry
    window_tool = registry.lookup("window_control")
    browser_tool = registry.lookup("browser_control")
    settings_tool = registry.lookup("computer_settings")

    assert window_tool is not None
    assert "close_tab" in window_tool.parameters["properties"]["action"]["description"]
    browser_actions = browser_tool.parameters["properties"]["action"]["description"]
    assert "close_tab" not in browser_actions
    assert "window_control" in settings_tool.description


def test_tab_title_matching_is_case_and_punctuation_insensitive():
    assert wc._title_matches("YouTube - Google Chrome", "youtube")
    assert wc._title_matches("Inbox (3) – Gmail", "Gmail")
    assert not wc._title_matches("GitHub - Google Chrome", "YouTube")
