"""
JARVIS Notes Access — cross-platform notes system.

macOS: Apple Notes via AppleScript (read + create only).
Windows/Linux: Local markdown notes in data/notes/ directory.

CANNOT edit or delete existing notes (safety by design).
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from platform_utils import IS_WINDOWS, IS_MACOS, NOTES_DIR

log = logging.getLogger("jarvis.notes")


# ---------------------------------------------------------------------------
# Local Markdown Notes (Windows/Linux)
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", text.lower())
    slug = re.sub(r"[\s]+", "-", slug.strip())
    return slug[:60] or "untitled"


def _read_note_file(path: Path) -> dict:
    """Read a markdown note file and extract metadata."""
    content = path.read_text(encoding="utf-8", errors="replace")
    title = path.stem.split("_", 1)[1] if "_" in path.stem else path.stem
    title = title.replace("-", " ").title()
    # Check for YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
            content = parts[2].strip()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return {
        "title": title,
        "body": content,
        "date": mtime.strftime("%Y-%m-%d %H:%M"),
        "folder": path.parent.name,
        "path": str(path),
    }


# ---------------------------------------------------------------------------
# macOS AppleScript helpers
# ---------------------------------------------------------------------------

async def _run_notes_script(script: str, timeout: float = 10) -> str:
    """Run an AppleScript against Notes.app."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            log.warning(f"Notes script failed: {stderr.decode()[:200]}")
            return ""
        return stdout.decode().strip()
    except asyncio.TimeoutError:
        log.warning("Notes script timed out")
        return ""
    except Exception as e:
        log.warning(f"Notes script error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Public API (cross-platform)
# ---------------------------------------------------------------------------

async def get_recent_notes(count: int = 10) -> list[dict]:
    """Get most recent notes (title + creation date)."""
    if IS_MACOS:
        script = f'''tell application "Notes"
    set output to ""
    set allNotes to every note
    set limit to count of allNotes
    if limit > {count} then set limit to {count}
    repeat with i from 1 to limit
        set n to item i of allNotes
        set output to output & name of n & "|||" & (creation date of n as string) & "|||" & name of container of n & linefeed
    end repeat
    return output
end tell'''
        raw = await _run_notes_script(script, timeout=15)
        if not raw:
            return []
        notes = []
        for line in raw.split("\n"):
            parts = line.strip().split("|||")
            if len(parts) >= 3:
                notes.append({"title": parts[0].strip(), "date": parts[1].strip(), "folder": parts[2].strip()})
        return notes

    # Windows/Linux: local markdown notes
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    note_files = sorted(NOTES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    notes = []
    for nf in note_files[:count]:
        try:
            info = _read_note_file(nf)
            notes.append({"title": info["title"], "date": info["date"], "folder": info["folder"]})
        except Exception:
            continue
    return notes


async def read_note(title_match: str) -> dict | None:
    """Read a note by title (partial match). Returns title + body."""
    if IS_MACOS:
        escaped = title_match.replace('"', '\\"')
        script = f'''tell application "Notes"
    set allNotes to every note
    repeat with n in allNotes
        if name of n contains "{escaped}" then
            set nBody to plaintext of n
            if length of nBody > 3000 then set nBody to text 1 thru 3000 of nBody
            return name of n & "|||" & nBody
        end if
    end repeat
    return ""
end tell'''
        raw = await _run_notes_script(script, timeout=10)
        if not raw or "|||" not in raw:
            return None
        title, _, body = raw.partition("|||")
        return {"title": title.strip(), "body": body.strip()}

    # Windows/Linux: search local notes
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    match_lower = title_match.lower()
    for nf in sorted(NOTES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            info = _read_note_file(nf)
            if match_lower in info["title"].lower() or match_lower in nf.stem.lower():
                return {"title": info["title"], "body": info["body"]}
        except Exception:
            continue
    return None


async def search_notes_apple(query: str, count: int = 5) -> list[dict]:
    """Search notes by title keyword."""
    if IS_MACOS:
        escaped = query.replace('"', '\\"')
        script = f'''tell application "Notes"
    set output to ""
    set foundCount to 0
    set allNotes to every note
    repeat with n in allNotes
        if foundCount >= {count} then exit repeat
        if name of n contains "{escaped}" then
            set output to output & name of n & "|||" & (creation date of n as string) & linefeed
            set foundCount to foundCount + 1
        end if
    end repeat
    return output
end tell'''
        raw = await _run_notes_script(script, timeout=15)
        if not raw:
            return []
        notes = []
        for line in raw.split("\n"):
            parts = line.strip().split("|||")
            if len(parts) >= 2:
                notes.append({"title": parts[0].strip(), "date": parts[1].strip()})
        return notes

    # Windows/Linux: full-text search
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    query_lower = query.lower()
    results = []
    for nf in sorted(NOTES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(results) >= count:
            break
        try:
            content = nf.read_text(encoding="utf-8", errors="replace").lower()
            if query_lower in content or query_lower in nf.stem.lower():
                info = _read_note_file(nf)
                results.append({"title": info["title"], "date": info["date"]})
        except Exception:
            continue
    return results


async def create_apple_note(title: str, body: str, folder: str = "Notes") -> bool:
    """Create a new note."""
    if IS_MACOS:
        html_body = _body_to_html(body)
        escaped_title = title.replace('"', '\\"')
        escaped_body = html_body.replace('"', '\\"')
        escaped_folder = folder.replace('"', '\\"')
        script = f'''tell application "Notes"
    tell folder "{escaped_folder}"
        make new note with properties {{name:"{escaped_title}", body:"{escaped_body}"}}
    end tell
    return "OK"
end tell'''
        result = await _run_notes_script(script, timeout=10)
        if result == "OK":
            log.info(f"Created Apple Note: {title}")
            return True
        return False

    # Windows/Linux: create markdown file
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(title)
    filename = f"{timestamp}_{slug}.md"
    filepath = NOTES_DIR / filename

    content = f"""---
title: "{title}"
created: {datetime.now().isoformat()}
folder: {folder}
---

{body}
"""
    try:
        filepath.write_text(content, encoding="utf-8")
        log.info(f"Created note: {title} -> {filepath}")
        return True
    except Exception as e:
        log.error(f"Failed to create note: {e}")
        return False


async def get_note_folders() -> list[str]:
    """Get list of note folder names."""
    if IS_MACOS:
        script = '''tell application "Notes"
    set output to ""
    repeat with f in every folder
        set output to output & name of f & linefeed
    end repeat
    return output
end tell'''
        raw = await _run_notes_script(script)
        return [f.strip() for f in raw.split("\n") if f.strip()]

    # Windows/Linux: subdirectories in notes dir
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    folders = ["Notes"]  # Default
    for d in NOTES_DIR.iterdir():
        if d.is_dir():
            folders.append(d.name)
    return folders


def _body_to_html(body: str) -> str:
    """Convert plain text / markdown to HTML for Apple Notes."""
    lines = body.split("\n")
    html_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append("<br>")
        elif re.match(r"^-\s*\[x\]\s*", stripped, re.IGNORECASE):
            text = re.sub(r"^-\s*\[x\]\s*", "", stripped, flags=re.IGNORECASE)
            html_lines.append(f'<div><input type="checkbox" checked="checked"> {text}</div>')
        elif re.match(r"^-\s*\[\s?\]\s*", stripped):
            text = re.sub(r"^-\s*\[\s?\]\s*", "", stripped)
            html_lines.append(f'<div><input type="checkbox"> {text}</div>')
        elif re.match(r"^[-*+]\s+", stripped):
            text = re.sub(r"^[-*+]\s+", "", stripped)
            html_lines.append(f"<div>• {text}</div>")
        elif re.match(r"^\d+\.\s+", stripped):
            html_lines.append(f"<div>{stripped}</div>")
        elif stripped.startswith("#"):
            text = re.sub(r"^#+\s*", "", stripped)
            html_lines.append(f"<h2>{text}</h2>")
        else:
            html_lines.append(f"<div>{stripped}</div>")
    return "\n".join(html_lines)
