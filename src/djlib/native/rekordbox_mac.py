"""Drive rekordbox's own menus on macOS: Import Playlist and Export Collection in xml format.

This uses rekordbox's supported import/export paths through macOS UI scripting; it never
reads or writes rekordbox's database. macOS requires the controlling app (e.g. Terminal) to
be allowed under System Settings > Privacy & Security > Accessibility.
"""

import subprocess
import sys
import time
from pathlib import Path

from djlib.domain.errors import AppError

APP_NAMES = ("rekordbox 7", "rekordbox 6", "rekordbox")
PROCESS = "rekordbox"
IMPORT_PLAYLIST = "Import Playlist"
EXPORT_XML = "Export Collection in xml format"

FIND_AND_CLICK = """
on findItem(theMenu, targetName)
  tell application "System Events"
    repeat with itm in menu items of theMenu
      if name of itm is targetName then return itm
      try
        set found to my findItem(menu 1 of itm, targetName)
        if found is not missing value then return found
      end try
    end repeat
  end tell
  return missing value
end findItem

on run argv
  set targetName to item 1 of argv
  tell application "System Events"
    tell process "rekordbox"
      set frontmost to true
      delay 0.3
      repeat with topMenu in menu bar items of menu bar 1
        try
          set found to my findItem(menu 1 of topMenu, targetName)
          if found is not missing value then
            if enabled of found is false then return "disabled"
            click found
            return "clicked"
          end if
        end try
      end repeat
    end tell
  end tell
  return "missing"
end run
"""

CHOOSE_PATH = """
on run argv
  set targetPath to item 1 of argv
  tell application "System Events"
    tell process "rekordbox"
      set frontmost to true
      delay 1.0
      keystroke "g" using {command down, shift down}
      delay 0.8
      keystroke targetPath
      delay 0.5
      key code 36
      delay 0.8
      key code 36
    end tell
  end tell
  return "chosen"
end run
"""

SAVE_AS = """
on run argv
  set folderPath to item 1 of argv
  set fileName to item 2 of argv
  tell application "System Events"
    tell process "rekordbox"
      set frontmost to true
      delay 1.0
      keystroke "a" using {command down}
      keystroke fileName
      delay 0.3
      keystroke "g" using {command down, shift down}
      delay 0.8
      keystroke folderPath
      delay 0.5
      key code 36
      delay 0.8
      key code 36
    end tell
  end tell
  return "saved"
end run
"""


def _osascript(script: str, *args: str, timeout: float = 30) -> str:
    try:
        done = subprocess.run(
            ["osascript", "-", *args],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AppError("APP_AUTOMATION_FAILED", "macOS UI scripting did not respond.") from exc
    if done.returncode:
        message = (done.stderr or "").strip()
        if "assistive" in message or "1002" in message or "-25211" in message:
            raise AppError(
                "APP_AUTOMATION_NOT_ALLOWED",
                "Allow your terminal app under System Settings > Privacy & Security > "
                "Accessibility, then retry.",
            )
        raise AppError("APP_AUTOMATION_FAILED", f"rekordbox automation failed: {message[:200]}")
    return done.stdout.strip()


def ensure_supported() -> None:
    if sys.platform != "darwin":
        raise AppError(
            "APP_AUTOMATION_UNSUPPORTED",
            "Driving rekordbox is only implemented on macOS so far.",
        )
    if _osascript('tell application "System Events" to get UI elements enabled') != "true":
        raise AppError(
            "APP_AUTOMATION_NOT_ALLOWED",
            "Allow your terminal app under System Settings > Privacy & Security > Accessibility, "
            "then retry.",
        )


def _running() -> bool:
    return _osascript(f'tell application "System Events" to exists process "{PROCESS}"') == "true"


def _window_count() -> int:
    value = _osascript(f'tell application "System Events" to count windows of process "{PROCESS}"')
    return int(value) if value.isdigit() else 0


def ensure_running(timeout: float = 120) -> None:
    """Launch rekordbox if needed and wait until its main window is up."""
    if not _running():
        for name in APP_NAMES:
            if subprocess.run(["open", "-a", name], capture_output=True).returncode == 0:
                break
        else:
            raise AppError("APP_NOT_INSTALLED", "rekordbox is not installed in /Applications.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _running() and _window_count() > 0:
            time.sleep(3)  # let the library finish loading before using menus
            return
        time.sleep(1)
    raise AppError("APP_NOT_READY", "rekordbox did not open a window in time.")


def click_menu(name: str) -> None:
    outcome = _osascript(FIND_AND_CLICK, name)
    if outcome == "missing":
        raise AppError("APP_MENU_MISSING", f"rekordbox has no “{name}” menu item.")
    if outcome == "disabled":
        raise AppError(
            "APP_MENU_DISABLED",
            f"“{name}” is unavailable; close any open rekordbox dialog and retry.",
        )


def import_playlist(playlist: Path) -> None:
    ensure_running()
    windows = _window_count()
    click_menu(IMPORT_PLAYLIST)
    _wait_for_dialog(windows)
    _osascript(CHOOSE_PATH, str(playlist))


def export_collection(destination: Path, timeout: float = 120) -> Path:
    ensure_running()
    destination.parent.mkdir(parents=True, exist_ok=True)
    windows = _window_count()
    click_menu(EXPORT_XML)
    _wait_for_dialog(windows)
    _osascript(SAVE_AS, str(destination.parent), destination.name)
    deadline = time.monotonic() + timeout
    previous = -1
    while time.monotonic() < deadline:
        if destination.is_file():
            size = destination.stat().st_size
            if size and size == previous:
                return destination
            previous = size
        time.sleep(1)
    raise AppError("APP_EXPORT_TIMEOUT", "rekordbox did not write the collection XML in time.")


def _wait_for_dialog(before: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _window_count() > before or _sheet_open():
            return
        time.sleep(0.3)
    # Some rekordbox builds reuse the main window for panels; continue and let the
    # subsequent keystrokes fail loudly if no file dialog is present.


def _sheet_open() -> bool:
    script = (
        f'tell application "System Events" to tell process "{PROCESS}" to '
        "exists sheet 1 of window 1"
    )
    try:
        return _osascript(script) == "true"
    except AppError:
        return False
