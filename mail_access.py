"""
JARVIS Mail Access — cross-platform READ-ONLY email access.

macOS: Apple Mail via AppleScript.
Windows: IMAP (works with Gmail, Outlook.com, Yahoo, etc.)

IMPORTANT: This module is intentionally READ-ONLY.
No send, delete, move, or modify functions exist by design.
"""

import asyncio
import logging
import os
from datetime import datetime

from platform_utils import IS_WINDOWS, IS_MACOS

log = logging.getLogger("jarvis.mail")

_mail_launched = False

# IMAP config for Windows
IMAP_SERVER = os.getenv("IMAP_SERVER", "")
IMAP_EMAIL = os.getenv("IMAP_EMAIL", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")
_imap_available = bool(IMAP_SERVER and IMAP_EMAIL and IMAP_PASSWORD)

if IS_WINDOWS and not _imap_available:
    # Try Outlook COM as fallback
    try:
        import win32com.client
        _outlook_mail_available = True
    except ImportError:
        _outlook_mail_available = False
        log.info("No mail source configured. Set IMAP_SERVER/IMAP_EMAIL/IMAP_PASSWORD in .env or install pywin32 for Outlook.")
else:
    _outlook_mail_available = False


# --- IMAP Implementation ---

def _imap_connect():
    import imaplib
    conn = imaplib.IMAP4_SSL(IMAP_SERVER)
    conn.login(IMAP_EMAIL, IMAP_PASSWORD)
    return conn

def _parse_email_message(raw_data):
    import email
    from email.header import decode_header
    msg = email.message_from_bytes(raw_data)
    subject = ""
    for part, enc in decode_header(msg.get("Subject", "")):
        subject += part.decode(enc or "utf-8") if isinstance(part, bytes) else str(part)
    sender = msg.get("From", "")
    date_str = msg.get("Date", "")
    # Get preview
    preview = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    preview = part.get_payload(decode=True).decode(errors="replace")[:150]
                except Exception:
                    pass
                break
    else:
        try:
            preview = msg.get_payload(decode=True).decode(errors="replace")[:150]
        except Exception:
            pass
    return {"sender": sender, "subject": subject, "date": date_str, "preview": preview.strip()}

async def _imap_get_unread_count() -> dict:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _imap_get_unread_count_sync)
    except Exception as e:
        log.warning(f"IMAP unread count failed: {e}")
        return {"total": 0, "accounts": {}}

def _imap_get_unread_count_sync() -> dict:
    conn = _imap_connect()
    conn.select("INBOX", readonly=True)
    _, data = conn.search(None, "UNSEEN")
    count = len(data[0].split()) if data[0] else 0
    conn.logout()
    return {"total": count, "accounts": {IMAP_EMAIL: count}}

async def _imap_get_messages(count: int = 10, unread_only: bool = False) -> list[dict]:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _imap_get_messages_sync, count, unread_only)
    except Exception as e:
        log.warning(f"IMAP messages failed: {e}")
        return []

def _imap_get_messages_sync(count: int, unread_only: bool) -> list[dict]:
    conn = _imap_connect()
    conn.select("INBOX", readonly=True)
    criteria = "UNSEEN" if unread_only else "ALL"
    _, data = conn.search(None, criteria)
    ids = data[0].split()
    if not ids:
        conn.logout()
        return []
    ids = ids[-count:]  # Last N
    messages = []
    for mid in reversed(ids):
        _, msg_data = conn.fetch(mid, "(RFC822)")
        if msg_data[0] is not None:
            parsed = _parse_email_message(msg_data[0][1])
            parsed["read"] = not unread_only
            messages.append(parsed)
    conn.logout()
    return messages

async def _imap_search(query: str, count: int = 10) -> list[dict]:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _imap_search_sync, query, count)
    except Exception as e:
        log.warning(f"IMAP search failed: {e}")
        return []

def _imap_search_sync(query: str, count: int) -> list[dict]:
    conn = _imap_connect()
    conn.select("INBOX", readonly=True)
    _, data = conn.search(None, f'(OR SUBJECT "{query}" FROM "{query}")')
    ids = data[0].split()
    if not ids:
        conn.logout()
        return []
    ids = ids[-count:]
    messages = []
    for mid in reversed(ids):
        _, msg_data = conn.fetch(mid, "(RFC822)")
        if msg_data[0] is not None:
            parsed = _parse_email_message(msg_data[0][1])
            parsed["read"] = True
            messages.append(parsed)
    conn.logout()
    return messages


# --- Outlook COM Implementation (Windows fallback) ---

async def _outlook_get_unread_count() -> dict:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _outlook_unread_sync)
    except Exception as e:
        log.warning(f"Outlook unread failed: {e}")
        return {"total": 0, "accounts": {}}

def _outlook_unread_sync() -> dict:
    import win32com.client, pythoncom
    pythoncom.CoInitialize()
    try:
        ol = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox = ol.GetDefaultFolder(6)  # olFolderInbox
        count = inbox.UnReadItemCount
        return {"total": count, "accounts": {"Outlook": count}}
    finally:
        pythoncom.CoUninitialize()

async def _outlook_get_messages(count: int = 10, unread_only: bool = False) -> list[dict]:
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _outlook_messages_sync, count, unread_only)
    except Exception as e:
        log.warning(f"Outlook messages failed: {e}")
        return []

def _outlook_messages_sync(count: int, unread_only: bool) -> list[dict]:
    import win32com.client, pythoncom
    pythoncom.CoInitialize()
    try:
        ol = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox = ol.GetDefaultFolder(6)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)
        messages = []
        for i, item in enumerate(items):
            if i >= count:
                break
            try:
                if unread_only and item.UnRead is False:
                    continue
                messages.append({
                    "sender": str(item.SenderName),
                    "subject": str(item.Subject),
                    "date": str(item.ReceivedTime),
                    "read": not item.UnRead,
                    "preview": str(item.Body)[:150] if hasattr(item, 'Body') else "",
                })
            except Exception:
                continue
        return messages
    finally:
        pythoncom.CoUninitialize()


# --- macOS: AppleScript ---

async def _ensure_mail_running():
    global _mail_launched
    if _mail_launched or IS_WINDOWS:
        return
    check = 'tell application "System Events" to return (name of every application process) contains "Mail"'
    try:
        proc = await asyncio.create_subprocess_exec("osascript", "-e", check,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if "true" in stdout.decode().lower():
            _mail_launched = True
            return
    except Exception:
        pass
    try:
        proc = await asyncio.create_subprocess_exec("open", "-a", "Mail", "-g",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=5)
        await asyncio.sleep(2)
        _mail_launched = True
    except Exception as e:
        log.warning(f"Failed to launch Mail: {e}")

async def _run_mail_script(script: str, timeout: float = 20) -> str:
    await _ensure_mail_running()
    try:
        proc = await asyncio.create_subprocess_exec("osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return ""
        return stdout.decode().strip()
    except Exception:
        return ""


# --- Public API ---

async def get_unread_count() -> dict:
    if IS_WINDOWS:
        if _imap_available:
            return await _imap_get_unread_count()
        elif _outlook_mail_available:
            return await _outlook_get_unread_count()
        return {"total": 0, "accounts": {}}
    # macOS
    script = '''tell application "Mail"
    set totalUnread to unread count of inbox
    return "total:" & totalUnread
end tell'''
    raw = await _run_mail_script(script)
    try:
        count = int(raw.split(":")[1].strip())
        return {"total": count, "accounts": {}}
    except Exception:
        return {"total": 0, "accounts": {}}

async def get_recent_messages(count: int = 10) -> list[dict]:
    if IS_WINDOWS:
        if _imap_available:
            return await _imap_get_messages(count, False)
        elif _outlook_mail_available:
            return await _outlook_get_messages(count, False)
        return []
    # macOS AppleScript (simplified)
    script = f'''tell application "Mail"
    set allMsgs to messages of inbox
    set msgCount to count of allMsgs
    set limit to msgCount
    if limit > {count} then set limit to {count}
    set output to ""
    repeat with i from 1 to limit
        set m to item i of allMsgs
        set output to output & sender of m & "|||" & subject of m & "|||" & (date received of m as string) & "|||" & read status of m & linefeed
    end repeat
    return output
end tell'''
    raw = await _run_mail_script(script, timeout=20)
    if not raw:
        return []
    messages = []
    for line in raw.split("\n"):
        parts = line.strip().split("|||")
        if len(parts) >= 4:
            messages.append({"sender": parts[0].strip(), "subject": parts[1].strip(),
                             "date": parts[2].strip(), "read": parts[3].strip().lower() == "true", "preview": ""})
    return messages

async def get_unread_messages(count: int = 10) -> list[dict]:
    if IS_WINDOWS:
        if _imap_available:
            return await _imap_get_messages(count, True)
        elif _outlook_mail_available:
            return await _outlook_get_messages(count, True)
        return []
    # macOS
    script = f'''tell application "Mail"
    set allMsgs to messages of inbox whose read status is false
    set msgCount to count of allMsgs
    set limit to msgCount
    if limit > {count} then set limit to {count}
    set output to ""
    repeat with i from 1 to limit
        set m to item i of allMsgs
        set output to output & sender of m & "|||" & subject of m & "|||" & (date received of m as string) & linefeed
    end repeat
    return output
end tell'''
    raw = await _run_mail_script(script, timeout=20)
    if not raw:
        return []
    messages = []
    for line in raw.split("\n"):
        parts = line.strip().split("|||")
        if len(parts) >= 3:
            messages.append({"sender": parts[0].strip(), "subject": parts[1].strip(),
                             "date": parts[2].strip(), "read": False, "preview": ""})
    return messages

async def search_mail(query: str, count: int = 10) -> list[dict]:
    if IS_WINDOWS:
        if _imap_available:
            return await _imap_search(query, count)
        return []
    escaped = query.replace('"', '\\"')
    script = f'''tell application "Mail"
    set output to ""
    set foundCount to 0
    set allMsgs to messages of inbox
    repeat with m in allMsgs
        if foundCount >= {count} then exit repeat
        if subject of m contains "{escaped}" or sender of m contains "{escaped}" then
            set output to output & sender of m & "|||" & subject of m & "|||" & (date received of m as string) & "|||" & read status of m & linefeed
            set foundCount to foundCount + 1
        end if
    end repeat
    return output
end tell'''
    raw = await _run_mail_script(script, timeout=30)
    if not raw:
        return []
    messages = []
    for line in raw.split("\n"):
        parts = line.strip().split("|||")
        if len(parts) >= 4:
            messages.append({"sender": parts[0].strip(), "subject": parts[1].strip(),
                             "date": parts[2].strip(), "read": parts[3].strip().lower() == "true"})
    return messages

async def read_message(subject_match: str) -> dict | None:
    if IS_WINDOWS:
        # Search and return first match with full body
        msgs = await search_mail(subject_match, 1)
        return msgs[0] if msgs else None
    escaped = subject_match.replace('"', '\\"')
    script = f'''tell application "Mail"
    set allMsgs to messages of inbox
    repeat with m in allMsgs
        if subject of m contains "{escaped}" then
            set c to content of m
            if length of c > 3000 then set c to text 1 thru 3000 of c
            return sender of m & "|||" & subject of m & "|||" & (date received of m as string) & "|||" & c
        end if
    end repeat
    return ""
end tell'''
    raw = await _run_mail_script(script, timeout=20)
    if not raw:
        return None
    parts = raw.split("|||", 3)
    if len(parts) >= 4:
        return {"sender": parts[0].strip(), "subject": parts[1].strip(),
                "date": parts[2].strip(), "content": parts[3].strip()}
    return None

async def get_accounts() -> list[str]:
    if IS_WINDOWS:
        if _imap_available:
            return [IMAP_EMAIL]
        return []
    script = 'tell application "Mail" to return name of every account'
    raw = await _run_mail_script(script)
    return [a.strip() for a in raw.split(",") if a.strip()] if raw else []

def format_unread_summary(unread: dict) -> str:
    total = unread["total"]
    if total == 0:
        return "Inbox is clear, sir. No unread messages."
    parts = [f"{c} in {a}" for a, c in unread.get("accounts", {}).items() if c > 0]
    if len(parts) == 1:
        return f"You have {total} unread {'message' if total == 1 else 'messages'} — {parts[0]}."
    elif parts:
        return f"You have {total} unread messages: {', '.join(parts)}."
    return f"You have {total} unread {'message' if total == 1 else 'messages'}."

def format_messages_for_context(messages: list[dict], label: str = "Recent emails") -> str:
    if not messages:
        return f"{label}: None."
    lines = [f"{label}:"]
    for m in messages[:10]:
        read_marker = "" if m.get("read") else " [UNREAD]"
        line = f"  - {m['sender']}: {m['subject']}{read_marker}"
        if m.get("date"):
            lines.append(line + f" ({m['date'][:20]})")
        else:
            lines.append(line)
    return "\n".join(lines)

def format_messages_for_voice(messages: list[dict]) -> str:
    if not messages:
        return "No messages to report, sir."
    count = len(messages)
    if count == 1:
        m = messages[0]
        sender = _short_sender(m["sender"])
        return f"One message from {sender}: {m['subject']}."
    summaries = [f"{_short_sender(m['sender'])} regarding {m['subject']}" for m in messages[:5]]
    result = f"You have {count} messages. " + ". ".join(summaries[:3])
    if count > 3:
        result += f". And {count - 3} more."
    return result

def _short_sender(sender: str) -> str:
    if "<" in sender:
        return sender.split("<")[0].strip().strip('"')
    if "@" in sender:
        return sender.split("@")[0]
    return sender
