from actions import confirm_gate
from actions import terminal_download as terminal
from actions.download_control import download_control


def teardown_function():
    confirm_gate.clear_pending()


def test_windows_install_prefers_winget(monkeypatch):
    monkeypatch.setattr(terminal.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        terminal.shutil, "which", lambda name: "C:/winget.exe" if name == "winget" else None
    )
    monkeypatch.setattr(terminal, "_find_winget_id", lambda executable, package: "Spotify.Spotify")

    plan = terminal.resolve_install_plan("Spotify")

    assert plan is not None
    assert plan.executable == "C:/winget.exe"
    assert plan.args[:4] == ("install", "--id", "Spotify.Spotify", "--exact")
    assert plan.browser_fallback


def test_claude_code_uses_verified_winget_identifier(monkeypatch):
    monkeypatch.setattr(terminal.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        terminal.shutil, "which", lambda name: "C:/winget.EXE" if name == "winget" else None
    )
    responses = iter(("", "Name  Id  Version\nClaude Code  Anthropic.ClaudeCode  1.0"))
    monkeypatch.setattr(terminal, "_run_catalog_command", lambda argv: next(responses))

    plan = terminal.resolve_install_plan("claude code CLI")

    assert plan is not None
    assert plan.args[:4] == ("install", "--id", "Anthropic.ClaudeCode", "--exact")
    assert plan.display.startswith("winget.exe install --id Anthropic.ClaudeCode")


def test_macos_install_uses_homebrew_cask(monkeypatch):
    monkeypatch.setattr(terminal.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        terminal.shutil, "which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None
    )
    monkeypatch.setattr(terminal, "_find_brew_cask", lambda executable, package: "firefox")

    plan = terminal.resolve_install_plan("firefox")

    assert plan is not None
    assert plan.args == ("install", "--cask", "firefox")


def test_linux_install_prefers_apt_and_sudo(monkeypatch):
    monkeypatch.setattr(terminal.platform, "system", lambda: "Linux")
    paths = {"apt-get": "/usr/bin/apt-get", "sudo": "/usr/bin/sudo"}
    monkeypatch.setattr(terminal.shutil, "which", paths.get)
    monkeypatch.setattr(terminal, "_find_linux_package", lambda executable, package: "vlc")

    plan = terminal.resolve_install_plan("vlc")

    assert plan is not None
    assert plan.executable == "sudo"
    assert plan.args == ("/usr/bin/apt-get", "install", "--yes", "vlc")


def test_linux_install_uses_argv_without_shell(monkeypatch):
    plan = terminal.TerminalPlan("apt-get", ("install", "--yes", "vlc"), "apt-get install --yes vlc")
    observed = {}

    class Completed:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(argv, **kwargs):
        observed.update(argv=argv, kwargs=kwargs)
        return Completed()

    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    ok, _ = terminal.run_plan(plan)

    assert ok
    assert observed["argv"] == ["apt-get", "install", "--yes", "vlc"]
    assert observed["kwargs"]["shell"] is False


def test_terminal_output_removes_progress_noise():
    output = "\x1b[2K████████ 99%\r\nNo package found matching criteria.\n"

    assert terminal._clean_output(output) == "No package found matching criteria."


def test_ambiguous_winget_results_are_not_guessed(monkeypatch):
    output = (
        "Name  Id  Version\n"
        "Custom Cursor  BLife.CustomCursor  1.0\n"
        "Cursor Helper  Example.CursorHelper  1.0"
    )
    monkeypatch.setattr(terminal, "_run_catalog_command", lambda argv: output)

    assert terminal._find_winget_id("winget", "Cursor") is None


def test_install_stages_confirmation_then_decline_returns_command(monkeypatch):
    plan = terminal.TerminalPlan(
        "winget", ("install", "Spotify"), "winget install Spotify", browser_fallback=True
    )
    monkeypatch.setattr(terminal, "resolve_install_plan", lambda package: plan)

    staged = download_control({"action": "install", "query": "Spotify"})
    declined = download_control({"cancel": True})

    assert staged.startswith("NEEDS_CONFIRM:")
    assert "winget install Spotify" in staged
    assert declined.startswith("CANCELLED_COMMAND:")
    assert "```shell\nwinget install Spotify\n```" in declined


def test_confirmed_plan_executes_only_after_confirmation(monkeypatch):
    plan = terminal.TerminalPlan("curl", ("https://example.com/a.zip",), "curl https://example.com/a.zip")
    monkeypatch.setattr(terminal, "resolve_cli_plan", lambda tool, args, destination: plan)
    calls = []
    monkeypatch.setattr(terminal, "run_plan", lambda selected: (calls.append(selected) or True, "saved"))

    staged = download_control({"action": "cli", "tool": "curl", "args": ["https://example.com/a.zip"]})
    assert staged.startswith("NEEDS_CONFIRM:")
    assert calls == []

    result = download_control({"confirm": True})
    assert result.startswith("SUCCESS:")
    assert calls == [plan]
