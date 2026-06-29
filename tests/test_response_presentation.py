from core.response_presentation import is_command_request


def test_detects_requests_for_install_commands():
    assert is_command_request("Give me the command to download Claude Code CLI")
    assert is_command_request("What is the PowerShell command to install it?")
    assert is_command_request("How do I install Claude Code using the terminal?")


def test_does_not_intercept_execution_requests():
    assert not is_command_request("Download Claude Code CLI for me")
    assert not is_command_request("Install Spotify")
    assert not is_command_request("Run npm install")
