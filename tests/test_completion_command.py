import os
from pathlib import Path

from click.testing import CliRunner

from cyberwave_cli.main import cli


def _completion_env(
    *,
    complete_var: str,
    instruction: str,
    comp_words: str,
    comp_cword: str,
) -> dict[str, str]:
    env = dict(os.environ)
    env[complete_var] = instruction
    env["COMP_WORDS"] = comp_words
    env["COMP_CWORD"] = comp_cword
    return env


def test_completion_generate_bash_contains_click_hook():
    runner = CliRunner()
    result = runner.invoke(cli, ["completion", "generate", "--shell", "bash"])

    assert result.exit_code == 0
    assert "_cyberwave_completion()" in result.output
    assert "complete -o nosort -F _cyberwave_completion cyberwave" in result.output


def test_completion_install_uses_detected_shell(tmp_path: Path):
    runner = CliRunner()
    rc_file = tmp_path / ".zshrc"

    result = runner.invoke(
        cli,
        ["completion", "install", "--rc-file", str(rc_file)],
        env={"SHELL": "/bin/zsh"},
    )

    assert result.exit_code == 0
    assert "Installed zsh completion" in result.output
    content = rc_file.read_text(encoding="utf-8")
    assert "# >>> cyberwave completion >>>" in content
    assert 'eval "$(_CYBERWAVE_COMPLETE=zsh_source cyberwave)"' in content
    assert "# <<< cyberwave completion <<<" in content


def test_completion_install_is_idempotent(tmp_path: Path):
    runner = CliRunner()
    rc_file = tmp_path / ".bashrc"

    first = runner.invoke(
        cli,
        ["completion", "install", "--shell", "bash", "--rc-file", str(rc_file)],
    )
    second = runner.invoke(
        cli,
        ["completion", "install", "--shell", "bash", "--rc-file", str(rc_file)],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "Completion already configured" in second.output

    content = rc_file.read_text(encoding="utf-8")
    assert content.count("# >>> cyberwave completion >>>") == 1
    assert content.count("# <<< cyberwave completion <<<") == 1


def test_completion_install_requires_known_shell_when_omitted():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["completion", "install"],
        env={"SHELL": "/usr/bin/fish"},
    )

    assert result.exit_code != 0
    assert "Could not detect your shell" in result.output
    assert "completion install --shell bash" in result.output
    assert "completion install --shell zsh" in result.output


def test_click_completion_outputs_nested_subcommands_and_flags():
    runner = CliRunner()
    complete_var = "_CYBERWAVE_COMPLETE"

    subcommand_result = runner.invoke(
        cli,
        [],
        env=_completion_env(
            complete_var=complete_var,
            instruction="bash_complete",
            comp_words="cyberwave edge dr",
            comp_cword="2",
        ),
        prog_name="cyberwave",
    )
    assert subcommand_result.exit_code == 0
    assert "plain,driver" in subcommand_result.output

    flag_result = runner.invoke(
        cli,
        [],
        env=_completion_env(
            complete_var=complete_var,
            instruction="bash_complete",
            comp_words="cyberwave edge logs --f",
            comp_cword="3",
        ),
        prog_name="cyberwave",
    )
    assert flag_result.exit_code == 0
    assert "plain,--follow" in flag_result.output
