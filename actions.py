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
PROJECT_DIR = Path(__file__).parent.resolve()


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
            # webbrowser.open can be flaky on some Windows setups;
            # cmd /c start reliably uses the default browser.
            subprocess.Popen(['cmd', '/c', 'start', '', url], shell=False)
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


async def open_path(path: str) -> dict:
    """Open a file or folder on the system."""
    try:
        # Resolve path
        raw = (path or "").strip().strip('"').strip("'")
        low = raw.lower()

        # Common Windows known folders
        if IS_WINDOWS:
            home = Path.home()
            known: dict[str, Path] = {
                "desktop": DESKTOP_PATH,
                "downloads": Path(os.environ.get("USERPROFILE", str(home))) / "Downloads",
                "documents": Path(os.environ.get("USERPROFILE", str(home))) / "Documents",
                "pictures": Path(os.environ.get("USERPROFILE", str(home))) / "Pictures",
                "music": Path(os.environ.get("USERPROFILE", str(home))) / "Music",
                "videos": Path(os.environ.get("USERPROFILE", str(home))) / "Videos",
            }
            shell_known: dict[str, str] = {
                "desktop": "shell:Desktop",
                "downloads": "shell:Downloads",
                "documents": "shell:Personal",
                "pictures": "shell:My Pictures",
                "music": "shell:My Music",
                "videos": "shell:My Video",
            }

            # If user said a known folder name, prefer shell: which works with redirection/OneDrive
            if low in shell_known:
                try:
                    subprocess.Popen(["explorer", shell_known[low]], shell=False)
                    return {"success": True, "confirmation": f"Opened {low}, sir."}
                except Exception:
                    # fallback to physical path
                    pass

            if low in known and known[low].exists():
                p = known[low]
            else:
                p = Path(raw).expanduser()
        else:
            p = Path(raw).expanduser()
        
        # If not absolute, try relative to Desktop / home
        if not p.is_absolute():
            # Check desktop first
            desktop_p = DESKTOP_PATH / raw
            if desktop_p.exists():
                p = desktop_p
            else:
                # Check home
                home_p = Path.home() / raw
                if home_p.exists():
                    p = home_p

        if not p.exists():
             return {"success": False, "confirmation": f"I couldn't find {path}, sir."}
        
        log.info(f"Opening path: {p}")
        if IS_WINDOWS:
            # Explorer is more consistent for folders
            subprocess.Popen(["explorer", str(p)], shell=False)
        else:
            subprocess.run(["open", str(p)])
            
        return {"success": True, "confirmation": f"Opened {p.name} for you, sir."}
    except Exception as e:
        log.error(f"open_path failed: {e}")
        return {"success": False, "confirmation": f"Had trouble opening that, sir."}


def _iter_windows_shortcuts() -> list[Path]:
    """Return likely Windows shortcut locations."""
    if not IS_WINDOWS:
        return []
    home = Path.home()
    candidates: list[Path] = [
        DESKTOP_PATH,
        home / "Desktop",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    out: list[Path] = []
    for c in candidates:
        if c and c.exists():
            out.append(c)
    return out


async def open_app(app_name: str) -> dict:
    """Open a Windows application by name.

    Strategy:
    - Match `.lnk` in Desktop + Start Menu programs
    - Fallback to `start` on known executable names
    """
    name = (app_name or "").strip().strip('"').strip("'")
    if not name:
        return {"success": False, "confirmation": "Which application should I open, sir?"}

    if not IS_WINDOWS:
        return {"success": False, "confirmation": "App launching is only implemented on Windows right now, sir."}

    norm = re.sub(r"\s+", " ", name).strip().lower()

    # 1) Shortcut match
    try:
        matches: list[Path] = []
        for root in _iter_windows_shortcuts():
            for p in root.rglob("*.lnk"):
                stem = p.stem.lower()
                if norm == stem or norm in stem:
                    matches.append(p)

        if matches:
            # Prefer closest match / shortest name
            matches.sort(key=lambda p: (len(p.stem), p.stem.lower()))
            target = matches[0]
            os.startfile(str(target))
            return {"success": True, "confirmation": f"Opened {target.stem}, sir."}
    except Exception as e:
        log.warning(f"shortcut scan failed: {e}")

    # 2) Common app fallbacks
    fallbacks: dict[str, list[str]] = {
        "brave": [
            "brave",
            r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        "chrome": [
            "chrome",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
        ],
        "edge": ["msedge"],
        "notepad": ["notepad"],
        "calculator": ["calc"],
    }

    keys = [norm]
    # handle "open brave browser"
    if "brave" in norm:
        keys.append("brave")
    if "chrome" in norm:
        keys.append("chrome")
    if "edge" in norm:
        keys.append("edge")

    for k in keys:
        if k in fallbacks:
            for cmd in fallbacks[k]:
                expanded = os.path.expandvars(cmd)
                try:
                    if expanded.lower().endswith(".exe") and Path(expanded).exists():
                        subprocess.Popen([expanded], shell=False)
                        return {"success": True, "confirmation": f"Opened {k}, sir."}
                    # Use start for PATH-resolvable commands
                    subprocess.Popen(["cmd", "/c", "start", "", expanded], shell=False)
                    return {"success": True, "confirmation": f"Opened {k}, sir."}
                except Exception:
                    continue

    return {"success": False, "confirmation": f"I couldn't find an app called {name}, sir."}


async def open_aider_in_project(project_dir: str, prompt: str) -> dict:
    """Open a terminal, cd to project dir, run Aider interactively.

    Writes the prompt to AIDER.md
    then launches aider.
    """
    # Write prompt to AIDER.md
    aider_md = Path(project_dir) / "AIDER.md"
    aider_md.write_text(f"# Task\n\n{prompt}\n\nBuild this completely. If web app, make index.html work standalone.\n")

    if IS_WINDOWS:
        try:
            # Use the aider executable from our virtual environment if it exists
            aider_exe = PROJECT_DIR / "jarvis_env" / "Scripts" / "aider.exe"
            aider_cmd = f'"{aider_exe}"' if aider_exe.exists() else "aider"
            
            cmd = f'start cmd /k "cd /d {project_dir} && {aider_cmd} --model gemini/gemini-1.5-flash --message-file AIDER.md"'
            subprocess.Popen(cmd, shell=True)
            return {
                "success": True,
                "confirmation": "Aider is running in a terminal, sir. You can watch the progress.",
            }
        except Exception as e:
            log.error(f"open_claude_in_project failed: {e}")
            return {
                "success": False,
                "confirmation": "Had trouble spawning Aider, sir.",
            }

    elif IS_MACOS:
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "cd {project_dir} && aider --model gemini/gemini-1.5-flash --message-file AIDER.md"\n'
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
            log.error(f"open_aider_in_project failed: {stderr.decode()}")
        else:
            await _mark_terminal_as_shadow()
        return {
            "success": success,
            "confirmation": "Aider is running in Terminal, sir. You can watch the progress."
            if success
            else "Had trouble spawning Aider, sir.",
        }

    return {"success": False, "confirmation": "Not supported on this platform, sir."}


async def prompt_existing_terminal(project_name: str, prompt: str) -> dict:
    """Send a prompt to a running Aider session.

    On Windows: We can't inject keystrokes into an existing terminal easily,
    so we spawn a new terminal with aider instead.
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

            # Spawn aider in the project directory with the prompt
            import shutil
            # Use venv aider
            aider_exe = PROJECT_DIR / "jarvis_env" / "Scripts" / "aider.exe"
            aider_cmd = f'"{aider_exe}"' if aider_exe.exists() else "aider"

            cmd = f'start cmd /k "cd /d {project_dir} && {aider_cmd} --model gemini/gemini-1.5-flash --message \\"{prompt[:100]}\\""'
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
    """Monitor an Aider build for completion. Notify via WebSocket when done."""
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
        result = await open_terminal("aider --model gemini/gemini-2.5-flash")
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
        # Create project folder on Desktop, spawn Aider
        project_name = _generate_project_name(target)
        project_dir = str(DESKTOP_PATH / project_name)
        os.makedirs(project_dir, exist_ok=True)
        result = await open_aider_in_project(project_dir, target)
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
