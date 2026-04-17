"""
JARVIS Platform Utilities — cross-platform abstraction layer.

Detects the current OS and provides platform-appropriate constants,
paths, and helper functions so the rest of the codebase can work
on both macOS and Windows without scattered if/else blocks.
"""

import sys
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform Detection
# ---------------------------------------------------------------------------

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

PLATFORM_NAME = "windows" if IS_WINDOWS else ("macos" if IS_MACOS else "linux")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DESKTOP_PATH = Path.home() / "Desktop"
PROJECT_DIR = Path(__file__).parent

# Default project directory — ~/Desktop on both platforms
PROJECTS_DIR = DESKTOP_PATH

# Data directory for JARVIS internal storage
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Notes directory for local markdown notes (Windows replacement for Apple Notes)
NOTES_DIR = DATA_DIR / "notes"
NOTES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Shell / Terminal
# ---------------------------------------------------------------------------

def get_terminal_command() -> list[str]:
    """Get the command to open a new terminal window."""
    if IS_WINDOWS:
        return ["cmd", "/c", "start", "cmd"]
    elif IS_MACOS:
        return ["open", "-a", "Terminal"]
    else:
        # Linux — try common terminals
        for term in ["gnome-terminal", "konsole", "xterm"]:
            import shutil
            if shutil.which(term):
                return [term]
        return ["xterm"]


def get_shell_exec_prefix() -> list[str]:
    """Get prefix for executing a command in a new terminal window."""
    if IS_WINDOWS:
        return ["cmd", "/c", "start", "cmd", "/k"]
    elif IS_MACOS:
        return []  # Uses osascript instead
    else:
        return ["bash", "-c"]


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

def open_url(url: str) -> bool:
    """Open a URL in the default browser. Cross-platform."""
    import webbrowser
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# User Agent
# ---------------------------------------------------------------------------

def get_user_agent() -> str:
    """Get a realistic user agent string for the current platform."""
    if IS_WINDOWS:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    elif IS_MACOS:
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    else:
        return (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
