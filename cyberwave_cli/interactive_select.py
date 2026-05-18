"""Interactive arrow-key TUI selectors (single and multi-select).

Scrollable, keyboard-driven menus using raw-mode TTY on POSIX, with
fallback to numbered prompts when non-interactive or non-POSIX.
"""

from __future__ import annotations

import shutil
import sys

from rich.console import Console
from rich.prompt import Prompt

console = Console()


def _select_with_arrows(title: str, options: list[str]) -> int:
    """Interactive arrow-key selector. Falls back to numeric prompt."""
    if not options:
        raise ValueError("options cannot be empty")

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        while True:
            raw = Prompt.ask("Select option number", default="1")
            try:
                chosen = int(raw) - 1
                if 0 <= chosen < len(options):
                    return chosen
            except ValueError:
                pass
            console.print(f"[red]Please enter a number between 1 and {len(options)}[/red]")

    try:
        import termios
        import tty
    except ImportError:
        # Non-POSIX fallback
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        while True:
            raw = Prompt.ask("Select option number", default="1")
            try:
                chosen = int(raw) - 1
                if 0 <= chosen < len(options):
                    return chosen
            except ValueError:
                pass
            console.print(f"[red]Please enter a number between 1 and {len(options)}[/red]")

    selected = 0
    scroll_offset = 0
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        term_height = shutil.get_terminal_size().lines
    except Exception:
        term_height = 24
    # Reserve lines for: title(1) + instructions(1) + blank(1) + scroll indicators(2)
    max_visible = max(5, term_height - 5)

    def _tty_write(text: str) -> None:
        """Write text in raw TTY mode using CRLF line endings."""
        sys.stdout.write(text.replace("\n", "\r\n"))

    def _render() -> None:
        nonlocal scroll_offset
        # Keep selected item within the visible viewport
        if selected < scroll_offset:
            scroll_offset = selected
        elif selected >= scroll_offset + max_visible:
            scroll_offset = selected - max_visible + 1

        _tty_write("\x1b[2J\x1b[H")
        _tty_write(f"{title}\n")
        _tty_write("Use \u2191/\u2193 and press Enter, q/Ctrl-C to abort\n\n")

        visible_end = min(scroll_offset + max_visible, len(options))

        if scroll_offset > 0:
            _tty_write(f"  \u2191 {scroll_offset} more above\n")

        for idx in range(scroll_offset, visible_end):
            prefix = "❯" if idx == selected else " "
            _tty_write(f"{prefix} {options[idx]}\n")

        remaining = len(options) - visible_end
        if remaining > 0:
            _tty_write(f"  \u2193 {remaining} more below\n")

        sys.stdout.flush()

    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        _render()
        while True:
            char = sys.stdin.read(1)
            if char in ("\r", "\n"):
                return selected
            if char in ("\x03", "q", "Q"):
                raise KeyboardInterrupt
            if char == "\x1b":
                nxt = sys.stdin.read(1)
                if nxt == "[":
                    arrow = sys.stdin.read(1)
                    if arrow == "A":
                        selected = (selected - 1) % len(options)
                        _render()
                    elif arrow == "B":
                        selected = (selected + 1) % len(options)
                        _render()
            elif char.lower() == "k":
                selected = (selected - 1) % len(options)
                _render()
            elif char.lower() == "j":
                selected = (selected + 1) % len(options)
                _render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def _select_multiple_with_arrows(title: str, options: list[str]) -> list[int]:
    """Interactive multi-select. Toggle with Space, confirm with Enter."""
    if not options:
        return []

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        raw = Prompt.ask(
            "Select one or more (comma-separated numbers, empty for none)",
            default="",
        ).strip()
        if not raw:
            return []
        selected: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                idx = int(part) - 1
            except ValueError:
                continue
            if 0 <= idx < len(options) and idx not in selected:
                selected.append(idx)
        return selected

    try:
        import termios
        import tty
    except ImportError:
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        raw = Prompt.ask(
            "Select one or more (comma-separated numbers, empty for none)",
            default="",
        ).strip()
        if not raw:
            return []
        selected_fallback: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                idx = int(part) - 1
            except ValueError:
                continue
            if 0 <= idx < len(options) and idx not in selected_fallback:
                selected_fallback.append(idx)
        return selected_fallback

    cursor = 0
    scroll_offset = 0
    selected: set[int] = set()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        term_height = shutil.get_terminal_size().lines
    except Exception:
        term_height = 24
    max_visible = max(5, term_height - 5)

    def _tty_write(text: str) -> None:
        """Write text in raw TTY mode using CRLF line endings."""
        sys.stdout.write(text.replace("\n", "\r\n"))

    def _render() -> None:
        nonlocal scroll_offset
        if cursor < scroll_offset:
            scroll_offset = cursor
        elif cursor >= scroll_offset + max_visible:
            scroll_offset = cursor - max_visible + 1

        _tty_write("\x1b[2J\x1b[H")
        _tty_write(f"{title}\n")
        _tty_write(
            "Use \u2191/\u2193 to move, Space to toggle, Enter to confirm, q/Ctrl-C to abort\n\n"
        )

        visible_end = min(scroll_offset + max_visible, len(options))

        if scroll_offset > 0:
            _tty_write(f"  \u2191 {scroll_offset} more above\n")

        for idx in range(scroll_offset, visible_end):
            cursor_mark = "❯" if idx == cursor else " "
            selected_mark = "[x]" if idx in selected else "[ ]"
            _tty_write(f"{cursor_mark} {selected_mark} {options[idx]}\n")

        remaining = len(options) - visible_end
        if remaining > 0:
            _tty_write(f"  \u2193 {remaining} more below\n")

        sys.stdout.flush()

    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        _render()
        while True:
            char = sys.stdin.read(1)
            if char in ("\x03", "q", "Q"):
                raise KeyboardInterrupt
            if char in ("\r", "\n"):
                return sorted(selected)
            if char == " ":
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
                _render()
                continue
            if char == "\x1b":
                nxt = sys.stdin.read(1)
                if nxt == "[":
                    arrow = sys.stdin.read(1)
                    if arrow == "A":
                        cursor = (cursor - 1) % len(options)
                        _render()
                    elif arrow == "B":
                        cursor = (cursor + 1) % len(options)
                        _render()
            elif char.lower() == "k":
                cursor = (cursor - 1) % len(options)
                _render()
            elif char.lower() == "j":
                cursor = (cursor + 1) % len(options)
                _render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\x1b[?25h")
        _tty_write("\n")
        sys.stdout.flush()
