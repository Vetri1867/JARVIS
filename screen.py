"""
SHADOW Screen Awareness — see what's on the user's screen.

Two capabilities:
1. Window/app list (Windows: ctypes Win32 API, macOS: AppleScript)
2. Screenshot → Gemini vision API (Windows: mss, macOS: screencapture)
"""

import asyncio
import base64
import logging
import tempfile
from pathlib import Path

from platform_utils import IS_WINDOWS, IS_MACOS

log = logging.getLogger("shadow.screen")


# ---------------------------------------------------------------------------
# Windows Implementation
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Callback type for EnumWindows
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _get_windows_list() -> list[dict]:
        """Enumerate visible windows using Win32 API."""
        windows = []
        foreground_hwnd = user32.GetForegroundWindow()

        def enum_callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True

            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
            title = title_buf.value

            # Skip empty/system windows
            if not title or title in ("Program Manager", "Windows Input Experience"):
                return True

            # Get process name
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            app_name = _get_process_name(pid.value)

            windows.append({
                "app": app_name,
                "title": title,
                "frontmost": hwnd == foreground_hwnd,
            })
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return windows

    def _get_process_name(pid: int) -> str:
        """Get process name from PID using Windows API."""
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:
                    # Use QueryFullProcessImageNameW (pure ctypes, no psutil needed)
                    size = wintypes.DWORD(260)
                    buf = ctypes.create_unicode_buffer(260)
                    result = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
                    if result:
                        # Extract just the filename without extension
                        full_path = buf.value
                        name = full_path.rsplit("\\", 1)[-1]
                        if name.lower().endswith(".exe"):
                            name = name[:-4]
                        return name
                except Exception:
                    pass
                finally:
                    kernel32.CloseHandle(handle)
        except Exception:
            pass
        # Fallback to psutil if available
        try:
            import psutil
            return psutil.Process(pid).name().replace(".exe", "")
        except Exception:
            pass
        return "Unknown"

    def _get_running_apps_list() -> list[str]:
        """Get list of running GUI application names."""
        try:
            import psutil
            apps = set()
            for proc in psutil.process_iter(["name", "status"]):
                try:
                    name = proc.info["name"]
                    if name and not name.startswith("svchost") and proc.info["status"] != "zombie":
                        apps.add(name.replace(".exe", ""))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return sorted(apps)
        except ImportError:
            return []

    async def _take_screenshot_win() -> str | None:
        """Take a screenshot using mss (fast, no deps beyond mss)."""
        try:
            import mss
            import mss.tools
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Primary monitor
                screenshot = sct.grab(monitor)
                # Convert to PNG bytes
                png_bytes = mss.tools.to_png(screenshot.rgb, screenshot.size)
                log.info(f"Screenshot captured: {len(png_bytes)} bytes")
                return base64.b64encode(png_bytes).decode()
        except ImportError:
            log.warning("mss not installed — pip install mss")
            return None
        except Exception as e:
            log.warning(f"Screenshot error: {e}")
            return None


# ---------------------------------------------------------------------------
# Public API (cross-platform)
# ---------------------------------------------------------------------------

async def get_active_windows() -> list[dict]:
    """Get list of visible windows with app name, window title, and position.

    Returns list of {"app": str, "title": str, "frontmost": bool}.
    """
    if IS_WINDOWS:
        try:
            return _get_windows_list()
        except Exception as e:
            log.warning(f"get_active_windows error: {e}")
            return []

    elif IS_MACOS:
        # Original macOS AppleScript implementation
        script = """
set windowList to ""
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    set visibleApps to every application process whose visible is true
    repeat with proc in visibleApps
        set appName to name of proc
        try
            set winCount to count of windows of proc
            if winCount > 0 then
                repeat with w in (windows of proc)
                    try
                        set winTitle to name of w
                        if winTitle is not "" and winTitle is not missing value then
                            set windowList to windowList & appName & "|||" & winTitle & "|||" & (appName = frontApp) & linefeed
                        end if
                    end try
                end repeat
            end if
        end try
    end repeat
end tell
return windowList
"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)

            if proc.returncode != 0:
                log.warning(f"get_active_windows failed: {stderr.decode()[:200]}")
                return []

            windows = []
            for line in stdout.decode().strip().split("\n"):
                parts = line.strip().split("|||")
                if len(parts) >= 3:
                    windows.append({
                        "app": parts[0].strip(),
                        "title": parts[1].strip(),
                        "frontmost": parts[2].strip().lower() == "true",
                    })
            return windows

        except asyncio.TimeoutError:
            log.warning("get_active_windows timed out")
            return []
        except Exception as e:
            log.warning(f"get_active_windows error: {e}")
            return []

    return []


async def get_running_apps() -> list[str]:
    """Get list of running application names (visible only)."""
    if IS_WINDOWS:
        return _get_running_apps_list()

    elif IS_MACOS:
        script = """
tell application "System Events"
    set appNames to name of every application process whose visible is true
    set output to ""
    repeat with a in appNames
        set output to output & a & linefeed
    end repeat
    return output
end tell
"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                return [a.strip() for a in stdout.decode().strip().split("\n") if a.strip()]
            return []
        except Exception as e:
            log.warning(f"get_running_apps error: {e}")
            return []

    return []


async def take_screenshot(display_only: bool = True) -> str | None:
    """Take a screenshot and return base64-encoded PNG.

    Args:
        display_only: If True, capture main display only. If False, all displays.

    Returns:
        Base64-encoded PNG string, or None on failure.
    """
    if IS_WINDOWS:
        return await _take_screenshot_win()

    elif IS_MACOS:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        try:
            cmd = ["screencapture", "-x"]  # -x = no sound
            if display_only:
                cmd.append("-m")  # main display only
            cmd.append(tmp_path)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode != 0 or not Path(tmp_path).exists():
                log.warning("Screenshot capture failed")
                return None

            data = Path(tmp_path).read_bytes()
            log.info(f"Screenshot captured: {len(data)} bytes")
            return base64.b64encode(data).decode()

        except asyncio.TimeoutError:
            log.warning("Screenshot timed out")
            return None
        except Exception as e:
            log.warning(f"Screenshot error: {e}")
            return None
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    return None


async def describe_screen(llm_client) -> str:
    """Describe what's on the user's screen.

    Tries screenshot + vision first. Falls back to window list + LLM summary.
    """
    # Try screenshot + vision
    screenshot_b64 = await take_screenshot()
    if screenshot_b64 and llm_client:
        try:
            response = await llm_client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"inline_data": {"mime_type": "image/png", "data": screenshot_b64}},
                            {"text": "What's on my screen right now?"}
                        ]
                    }
                ],
                config={
                    "system_instruction": (
                        "You are SHADOW analyzing a screenshot of the user's desktop. "
                        "Describe what you see concisely: which apps are open, what the user "
                        "appears to be working on, any notable content visible. "
                        "Be specific about app names, file names, URLs, code, or documents visible. "
                        "2-4 sentences max. No markdown."
                    ),
                    "max_output_tokens": 300,
                }
            )
            return response.text
        except Exception as e:
            log.warning(f"Vision call failed, falling back to window list: {e}")

    # Fallback: get window list and have LLM summarize
    windows = await get_active_windows()
    apps = await get_running_apps()

    if not windows and not apps:
        return "I wasn't able to see your screen, sir. Screen access may be restricted."

    # Build a text description for LLM to summarize
    context_parts = []
    if windows:
        for w in windows:
            marker = " (ACTIVE)" if w["frontmost"] else ""
            context_parts.append(f"{w['app']}: {w['title']}{marker}")

    if apps:
        window_apps = set(w["app"] for w in windows) if windows else set()
        bg_apps = [a for a in apps if a not in window_apps]
        if bg_apps:
            context_parts.append(f"Background apps: {', '.join(bg_apps)}")

    if llm_client and context_parts:
        try:
            response = await llm_client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents="Open windows:\n" + "\n".join(context_parts),
                config={
                    "system_instruction": (
                        "You are SHADOW. Given the user's open windows and apps, summarize "
                        "what they appear to be working on in 1-2 sentences. Natural voice, no markdown."
                    ),
                    "max_output_tokens": 100,
                }
            )
            return response.text
        except Exception:
            pass

    # Raw fallback
    if windows:
        active = next((w for w in windows if w["frontmost"]), None)
        result = f"You have {len(windows)} windows open across {len(set(w['app'] for w in windows))} apps."
        if active:
            result += f" Currently focused on {active['app']}: {active['title']}."
        return result

    return f"Running apps: {', '.join(apps)}. Couldn't read window titles, sir."


def format_windows_for_context(windows: list[dict]) -> str:
    """Format window list as context string for the LLM."""
    if not windows:
        return ""
    lines = ["Currently open on your desktop:"]
    for w in windows:
        marker = " (active)" if w["frontmost"] else ""
        lines.append(f"  - {w['app']}: {w['title']}{marker}")
    return "\n".join(lines)
