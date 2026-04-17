"""Quick test of all ported modules."""
import asyncio

async def main():
    # 1. Platform utils
    from platform_utils import IS_WINDOWS, IS_MACOS, DESKTOP_PATH, NOTES_DIR
    print(f"[OK] platform_utils: Windows={IS_WINDOWS}, Desktop={DESKTOP_PATH}")

    # 2. Screen awareness
    from screen import get_active_windows, take_screenshot
    windows = await get_active_windows()
    print(f"[OK] screen: Found {len(windows)} windows")
    for w in windows[:3]:
        print(f"     {w['app']}: {w['title'][:50]}")

    # 3. Notes
    from notes_access import create_apple_note, get_recent_notes, read_note
    created = await create_apple_note("SHADOW Test Note", "Hello from SHADOW on Windows!")
    print(f"[OK] notes: Created test note = {created}")
    notes = await get_recent_notes()
    print(f"[OK] notes: Found {len(notes)} notes")

    # 4. Actions
    from actions import open_browser
    print("[OK] actions: imported successfully")

    # 5. Calendar
    from calendar_access import get_todays_events
    print("[OK] calendar_access: imported successfully")

    # 6. Mail
    from mail_access import get_unread_count
    print("[OK] mail_access: imported successfully")

    # 7. Work mode
    from work_mode import WorkSession
    print("[OK] work_mode: imported successfully")

    # 8. Browser
    from browser import ShadowBrowser
    print("[OK] browser: imported successfully")

    # 9. Planner
    from planner import TaskPlanner
    print("[OK] planner: imported successfully")

    # 10. Memory
    from memory import init_db, remember, recall
    print("[OK] memory: imported successfully")

    # 11. Dispatch registry
    from dispatch_registry import DispatchRegistry
    print("[OK] dispatch_registry: imported successfully")

    print("\n=== ALL MODULES IMPORTED SUCCESSFULLY ===")

asyncio.run(main())
