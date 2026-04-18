"""
SHADOW Work Mode — persistent aider sessions tied to projects.

SHADOW can connect to any project directory and maintain a conversation
with Aider. Aider automatically resumes the conversation history
in that directory, so context persists across messages.

The user sees Aider working in their Terminal window.
SHADOW reads the responses via subprocess, summarizes, and reports back.
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger("shadow.work_mode")

SESSION_FILE = Path(__file__).parent / "data" / "active_session.json"


class WorkSession:
    """An aider session tied to a project directory.

    Each project gets its own session. SHADOW can switch between projects
    and Aider picks up where the last message left off automatically.
    """

    def __init__(self):
        self._active = False
        self._working_dir: str | None = None
        self._project_name: str | None = None
        self._message_count = 0  # Track if this is first message (no --continue)
        self._status = "idle"  # idle, working, done

    @property
    def active(self) -> bool:
        return self._active

    @property
    def project_name(self) -> str | None:
        return self._project_name

    @property
    def status(self) -> str:
        return self._status

    async def start(self, working_dir: str, project_name: str = None):
        """Start or switch to a project session."""
        self._working_dir = working_dir
        self._project_name = project_name or Path(working_dir).name
        self._active = True
        self._message_count = 0
        self._status = "idle"
        log.info(f"Work mode started: {self._project_name} ({working_dir})")

    async def send(self, user_text: str) -> str:
        """Send a message to aider and get the full response.

        Aider automatically picks up context from the project directory.
        """
        aider_path = shutil.which("aider")
        if not aider_path:
            return "Aider CLI not found on this system."

        cmd = [
            aider_path,
            "--model", "gemini/gemini-2.5-flash",
            "--no-auto-commits",
            "--yes-always",
        ]

        self._status = "working"

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_dir,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=user_text.encode()),
                timeout=300,
            )

            response = stdout.decode().strip()
            self._message_count += 1
            self._status = "done"

            if process.returncode != 0:
                error = stderr.decode().strip()[:200]
                log.error(f"aider error: {error}")
                self._status = "error"
                return f"Hit a problem, sir: {error}"

            log.info(f"Aider response for {self._project_name} ({len(response)} chars)")
            return response

        except asyncio.TimeoutError:
            log.error("aider timed out after 300s")
            self._status = "timeout"
            return "That's taking longer than expected, sir. The operation timed out."
        except Exception as e:
            log.error(f"Work mode error: {e}")
            self._status = "error"
            return f"Something went wrong, sir: {str(e)[:100]}"

    async def stop(self):
        """End the work session."""
        project = self._project_name
        self._active = False
        self._working_dir = None
        self._project_name = None
        self._message_count = 0
        self._status = "idle"
        log.info(f"Work mode ended for {project}")

    def _save_session(self):
        """Persist session state so it survives restarts."""
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps({
                "project_name": self._project_name,
                "working_dir": self._working_dir,
                "message_count": self._message_count,
            }))
        except Exception as e:
            log.debug(f"Failed to save session: {e}")

    def _clear_session(self):
        """Remove persisted session."""
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    async def restore(self) -> bool:
        """Restore session from disk after restart. Returns True if restored."""
        try:
            if SESSION_FILE.exists():
                data = json.loads(SESSION_FILE.read_text())
                self._working_dir = data["working_dir"]
                self._project_name = data["project_name"]
                self._message_count = data.get("message_count", 1)  # Assume at least 1 so --continue is used
                self._active = True
                self._status = "idle"
                log.info(f"Restored work session: {self._project_name} ({self._working_dir})")
                return True
        except Exception as e:
            log.debug(f"No session to restore: {e}")
        return False


def is_casual_question(text: str) -> bool:
    """Detect if a message is casual chat vs work-related.

    Casual questions go to Gemini Flash (fast). Work goes to aider (powerful).
    """
    t = text.lower().strip()

    casual_patterns = [
        "what time", "what's the time", "what day",
        "what's the weather", "weather",
        "how are you", "are you there", "hey shadow",
        "good morning", "good evening", "good night",
        "thank you", "thanks", "never mind", "nevermind",
        "stop", "cancel", "quit work mode", "exit work mode",
        "go back to chat", "regular mode",
        "how's it going", "what's up",
        "are you still there", "you there", "shadow",
        "are you doing it", "is it working", "what happened",
        "did you hear me", "hello", "hey",
        "how's that coming", "hows that coming",
        "any update", "status update",
    ]

    # Short greetings/acknowledgments
    if len(t.split()) <= 3 and any(w in t for w in ["ok", "okay", "sure", "yes", "no", "yeah", "nah", "cool"]):
        return True

    return any(p in t for p in casual_patterns)
