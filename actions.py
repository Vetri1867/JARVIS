"""
SHADOW Action Executor — system actions (cross-platform).

macOS: AppleScript-based actions.
Windows: subprocess + webbrowser + ctypes.

Execute actions IMMEDIATELY, before generating any LLM response.
Each function returns {"success": bool, "confirmation": str}.
"""

import asyncio
import logging
import os
import re
import subprocess
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from platform_utils import IS_WINDOWS, IS_MACOS, DESKTOP_PATH

log = logging.getLogger("shadow.actions")


# ---------------------------------------------------------------------------
# Terminal Actions
# ---------------------------------------------------------------------------

async def open_terminal(command: str = "") -> dict:
    """Open a terminal window and optionally run a command."""
    if IS_WINDOWS:
        try:
            if command:
                # Open cmd with the command — /k keeps the window open
                subprocess.Popen(
                    f'start cmd /k "{command}"',
                    shell=True,
                    cwd=str(Path.home()),
                )
            else:
                subprocess.Popen("start cmd", shell=True, cwd=str(Path.home()))
            return {
                "success": True,
                "confirmation": "Terminal is open, sir.",
            }
        except Exception as e:
            log.error(f"open_terminal failed: {e}")
            return {
                "success": False,
                "confirmation": "I had trouble opening the terminal, sir.",
            }

    elif IS_MACOS:
        if command:
            escaped = command.replace('"', '\\"')
            script = (
                'tell application "Terminal"\n'
                "    activate\n"
                f'    do script "{escaped}"\n'
                "end tell"
            )
        else:
            script = (
                'tell application "Terminal"\n'
                "    activate\n"
                "end tell"
            )
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        success = proc.returncode == 0
        if not success:
            log.error(f"open_terminal failed: {stderr.decode()}")
        else:
            await _mark_terminal_as_shadow()
        return {
            "success": success,
            "confirmation": "Terminal is open, sir." if success else "I had trouble opening Terminal, sir.",
        }

    return {"success": False, "confirmation": "Terminal not supported on this platform, sir."}


async def open_browser(url: str, browser: str = "chrome") -> dict:
    """Open URL in the user's browser."""
    if IS_WINDOWS:
        try:
            webbrowser.open(url)
            return {
                "success": True,
                "confirmation": "Pulled that up in your browser, sir.",
            }
        except Exception as e:
            log.error(f"open_browser failed: {e}")
            return {
                "success": False,
                "confirmation": "Had trouble opening the browser, sir.",
            }

    elif IS_MACOS:
        escaped_url = url.replace('"', '\\"')

        if browser.lower() == "firefox":
            app_name = "Firefox"
            script = (
                'tell application "Firefox"\n'
                "    activate\n"
                f'    open location "{escaped_url}"\n'
                "end tell"
            )
        else:
            app_name = "Chrome"
            script = (
                'tell application "Google Chrome"\n'
                "    activate\n"
                f'    open location "{escaped_url}"\n'
                "end tell"
            )

        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        success = proc.returncode == 0
        if not success:
            log.error(f"open_browser ({app_name}) failed: {stderr.decode()}")
        return {
            "success": success,
            "confirmation": f"Pulled that up in {app_name}, sir." if success else f"{app_name} ran into a problem, sir.",
        }

    return {"success": False, "confirmation": "Browser not supported on this platform, sir."}


# Keep backward compat
async def open_chrome(url: str) -> dict:
    return await open_browser(url, "chrome")


async def open_claude_in_project(project_dir: str, prompt: str) -> dict:
    """Open a terminal, cd to project dir, run Claude Code interactively.

    Writes the prompt to CLAUDE.md (which claude reads automatically on startup)
    then launches claude in interactive mode with --dangerously-skip-permissions.
    """
    # Write prompt to CLAUDE.md — claude reads this automatically
    claude_md = Path(project_dir) / "CLAUDE.md"
    claude_md.write_text(f"# Task\n\n{prompt}\n\nBuild this completely. If web app, make index.html work standalone.\n")

    if IS_WINDOWS:
        try:
            # Launch claude interactive in a new cmd window
            cmd = f'start cmd /k "cd /d {project_dir} && claude --dangerously-skip-permissions"'
            subprocess.Popen(cmd, shell=True)
            return {
                "success": True,
                "confirmation": "Claude Code is running in a terminal, sir. You can watch the progress.",
            }
        except Exception as e:
            log.error(f"open_claude_in_project failed: {e}")
            return {
                "success": False,
                "confirmation": "Had trouble spawning Claude Code, sir.",
            }

    elif IS_MACOS:
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "cd {project_dir} && claude --dangerously-skip-permissions"\n'
            "end tell"
        )
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        success = proc.returncode == 0
        if not success:
            log.error(f"open_claude_in_project failed: {stderr.decode()}")
        else:
            await _mark_terminal_as_shadow()
        return {
            "success": success,
            "confirmation": "Claude Code is running in Terminal, sir. You can watch the progress."
            if success
            else "Had trouble spawning Claude Code, sir.",
        }

    return {"success": False, "confirmation": "Not supported on this platform, sir."}


async def prompt_existing_terminal(project_name: str, prompt: str) -> dict:
    """Send a prompt to a running Claude Code session.

    On Windows: We can't inject keystrokes into an existing terminal easily,
    so we spawn a new terminal with claude -p instead.
    On macOS: Uses System Events keystroke injection.
    """
    if IS_WINDOWS:
        try:
            # Find the project directory
            project_dir = None
            for d in DESKTOP_PATH.iterdir():
                if d.is_dir() and project_name.lower() in d.name.lower():
                    project_dir = str(d)
                    break

            if not project_dir:
                return {
                    "success": False,
                    "confirmation": f"Couldn't find a project for {project_name}, sir.",
                }

            # Spawn claude -p in the project directory with the prompt
            import shutil
            claude_path = shutil.which("claude")
            if not claude_path:
                return {
                    "success": False,
                    "confirmation": "Claude CLI not found on this system, sir.",
                }

            cmd = f'start cmd /k "cd /d {project_dir} && echo {prompt[:100]} | claude -p --dangerously-skip-permissions"'
            subprocess.Popen(cmd, shell=True)

            return {
                "success": True,
                "confirmation": f"Sent that to {project_name}, sir.",
            }
        except Exception as e:
            log.error(f"prompt_existing_terminal failed: {e}")
            return {"success": False, "confirmation": f"Something went wrong reaching {project_name}, sir."}

    elif IS_MACOS:
        escaped_name = project_name.replace('"', '\\"')
        escaped_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')

        script = f'''
tell application "Terminal"
    set matched to false
    set targetWindow to missing value
    repeat with w in windows
        if name of w contains "{escaped_name}" then
            set targetWindow to w
            set matched to true
            exit repeat
        end if
    end repeat

    if not matched then
        return "NOT_FOUND"
    end if

    set index of targetWindow to 1
    set selected tab of targetWindow to selected tab of targetWindow
    activate
end tell

delay 1

tell application "System Events"
    tell process "Terminal"
        set frontmost to true
        delay 0.3
        keystroke "{escaped_prompt}"
        delay 0.2
        keystroke return
    end tell
end tell

return "OK"
'''
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

            result = stdout.decode().strip()
            if result == "NOT_FOUND":
                return {
                    "success": False,
                    "confirmation": f"Couldn't find a terminal for {project_name}, sir.",
                }

            success = proc.returncode == 0
            if not success:
                log.error(f"prompt_existing_terminal failed: {stderr.decode()[:200]}")
            if success:
                await _mark_terminal_as_shadow()

            return {
                "success": success,
                "confirmation": f"Sent that to {project_name}, sir." if success
                else f"Had trouble typing into {project_name}, sir.",
            }

        except asyncio.TimeoutError:
            return {"success": False, "confirmation": "Terminal operation timed out, sir."}
        except Exception as e:
            log.error(f"prompt_existing_terminal failed: {e}")
            return {"success": False, "confirmation": "Something went wrong reaching that terminal, sir."}

    return {"success": False, "confirmation": "Not supported on this platform, sir."}


async def get_chrome_tab_info() -> dict:
    """Read the current Chrome tab's title and URL.

    Only available on macOS via AppleScript. Returns empty dict on Windows.
    """
    if IS_MACOS:
        script = (
            'tell application "Google Chrome"\n'
            "    set tabTitle to title of active tab of front window\n"
            "    set tabURL to URL of active tab of front window\n"
            '    return tabTitle & "|" & tabURL\n'
            "end tell"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                result = stdout.decode().strip()
                parts = result.split("|", 1)
                if len(parts) == 2:
                    return {"title": parts[0], "url": parts[1]}
            return {}
        except Exception as e:
            log.warning(f"get_chrome_tab_info failed: {e}")
            return {}

    # Windows: Not easily accessible without browser extensions
    return {}


async def monitor_build(project_dir: str, ws=None, synthesize_fn=None) -> None:
    """Monitor a Claude Code build for completion. Notify via WebSocket when done."""
    import base64

    output_file = Path(project_dir) / ".shadow_output.txt"
    start = time.time()
    timeout = 600  # 10 minutes

    while time.time() - start < timeout:
        await asyncio.sleep(5)
        if output_file.exists():
            content = output_file.read_text()
            if "--- SHADOW TASK COMPLETE ---" in content:
                log.info(f"Build complete in {project_dir}")
                if ws and synthesize_fn:
                    try:
                        msg = "The build is complete, sir."
                        audio_bytes = await synthesize_fn(msg)
                        if audio_bytes:
                            encoded = base64.b64encode(audio_bytes).decode()
                            await ws.send_json({"type": "status", "state": "speaking"})
                            await ws.send_json({"type": "audio", "data": encoded, "text": msg})
                            await ws.send_json({"type": "status", "state": "idle"})
                    except Exception as e:
                        log.warning(f"Build notification failed: {e}")
                return

    log.warning(f"Build timed out in {project_dir}")


async def execute_action(intent: dict, projects: list = None) -> dict:
    """Route a classified intent to the right action function.

    Args:
        intent: {"action": str, "target": str} from classify_intent()
        projects: list of known project dicts for resolving working dirs

    Returns: {"success": bool, "confirmation": str, "project_dir": str | None}
    """
    action = intent.get("action", "chat")
    target = intent.get("target", "")

    if action == "open_terminal":
        result = await open_terminal("claude --dangerously-skip-permissions")
        result["project_dir"] = None
        return result

    elif action == "browse":
        if target.startswith("http://") or target.startswith("https://"):
            url = target
        else:
            url = f"https://www.google.com/search?q={quote(target)}"

        # Detect which browser user wants
        target_lower = target.lower()
        if "firefox" in target_lower:
            browser = "firefox"
        else:
            browser = "chrome"

        result = await open_browser(url, browser)
        result["project_dir"] = None
        return result

    elif action == "build":
        # Create project folder on Desktop, spawn Claude Code
        project_name = _generate_project_name(target)
        project_dir = str(DESKTOP_PATH / project_name)
        os.makedirs(project_dir, exist_ok=True)
        result = await open_claude_in_project(project_dir, target)
        result["project_dir"] = project_dir
        return result

    else:
        return {"success": False, "confirmation": "", "project_dir": None}


def _generate_project_name(prompt: str) -> str:
    """Generate a kebab-case project folder name from the prompt."""
    # First: check for a quoted name like "tiktok-analytics-dashboard"
    quoted = re.search(r'"([^"]+)"', prompt)
    if quoted:
        name = quoted.group(1).strip()
        name = re.sub(r"[^a-zA-Z0-9\s-]", "", name).strip()
        if name:
            return re.sub(r"[\s]+", "-", name.lower())

    # Second: check for "called X" or "named X" pattern
    called = re.search(r'(?:called|named)\s+(\S+(?:[-_]\S+)*)', prompt, re.IGNORECASE)
    if called:
        name = re.sub(r"[^a-zA-Z0-9-]", "", called.group(1))
        if len(name) > 3:
            return name.lower()

    # Fallback: extract meaningful words
    words = re.sub(r"[^a-zA-Z0-9\s]", "", prompt.lower()).split()
    skip = {"a", "the", "an", "me", "build", "create", "make", "for", "with", "and",
            "to", "of", "i", "want", "need", "new", "project", "directory", "called",
            "on", "desktop", "that", "application", "app", "full", "stack", "simple",
            "web", "page", "site", "named"}
    meaningful = [w for w in words if w not in skip and len(w) > 2][:4]
    return "-".join(meaningful) if meaningful else "shadow-project"


# ---------------------------------------------------------------------------
# macOS-only Helpers
# ---------------------------------------------------------------------------

async def _mark_terminal_as_shadow(revert_after: float = 5.0):
    """Temporarily set the front Terminal window to Ocean theme, then revert.

    macOS only — no-op on Windows.
    """
    if not IS_MACOS:
        return

    script_save = (
        'tell application "Terminal"\n'
        '    return name of current settings of front window\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_save,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        original_profile = stdout.decode().strip()

        script_set = (
            'tell application "Terminal"\n'
            '    set current settings of front window to settings set "Ocean"\n'
            'end tell'
        )
        proc2 = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_set,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()

        if original_profile and original_profile != "Ocean":
            asyncio.get_event_loop().call_later(
                revert_after,
                lambda: asyncio.ensure_future(_revert_terminal_theme(original_profile))
            )
    except Exception:
        pass


async def _revert_terminal_theme(profile_name: str):
    """Revert a Terminal window back to its original profile. macOS only."""
    if not IS_MACOS:
        return
    escaped = profile_name.replace('"', '\\"')
    script = (
        'tell application "Terminal"\n'
        f'    set current settings of front window to settings set "{escaped}"\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass
