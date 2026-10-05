"""Drive rekordbox's own menus on macOS: Import Playlist and Export Collection in xml format.

This uses rekordbox's supported import/export paths through macOS UI scripting; it never
reads or writes rekordbox's database. macOS requires the controlling app (e.g. Terminal) to
be allowed under System Settings > Privacy & Security > Accessibility.
"""

import contextlib
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

# File dialogs are driven through accessibility values and named buttons. The only two
# keystrokes (Go to Folder, Return) are sent after checking that rekordbox is frontmost and
# the expected dialog has focus, so nothing is ever typed into another app.
PANEL = """
on focusedOn(w)
  tell application "System Events"
    tell process "rekordbox"
      if frontmost is false then return false
      set fw to value of attribute "AXFocusedWindow"
      try
        if (role of fw as text) is "AXSheet" then return true
      end try
      return (name of fw as text) is (name of w as text)
    end tell
  end tell
end focusedOn

on run argv
  set windowName to item 1 of argv
  set folderPath to item 2 of argv
  set fileName to item 3 of argv
  set confirmName to item 4 of argv
  tell application "rekordbox" to activate
  delay 0.5
  tell application "System Events"
    tell process "rekordbox"
      set w to window windowName
      perform action "AXRaise" of w
      delay 0.3
      set panel to splitter group 1 of w
      if fileName is not "" then
        set value of text field "Save As:" of panel to fileName
        delay 0.2
      end if
      -- A dialog that has just opened can drop keystrokes; retry the guarded shortcut.
      delay 0.8
      repeat with attempt from 1 to 3
        tell application "rekordbox" to activate
        perform action "AXRaise" of w
        delay 0.3
        if not my focusedOn(w) then return "not-focused"
        keystroke "g" using {command down, shift down}
        repeat 15 times
          if (count of sheets of w) > 0 then exit repeat
          delay 0.2
        end repeat
        if (count of sheets of w) > 0 then exit repeat
      end repeat
      if (count of sheets of w) = 0 then return "no-go-to-sheet"
      set value of text field 1 of sheet 1 of w to folderPath
      delay 0.4
      tell application "rekordbox" to activate
      if not my focusedOn(w) then return "not-focused"
      key code 36
      repeat 25 times
        if (count of sheets of w) = 0 then exit repeat
        delay 0.2
      end repeat
      delay 0.5
      if enabled of button confirmName of panel is false then return "confirm-disabled"
      click button confirmName of panel
      return "done"
    end tell
  end tell
end run
"""

DIALOG = """
tell application "System Events"
  tell process "rekordbox"
    repeat with w in windows
      if (subrole of w as text) is "AXDialog" then return name of w as text
    end repeat
  end tell
end tell
return ""
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
    click_menu(IMPORT_PLAYLIST)
    _drive_panel(_dialog(), str(playlist), "", "Open")


def export_collection(destination: Path, timeout: float = 180) -> Path:
    ensure_running()
    destination.parent.mkdir(parents=True, exist_ok=True)
    click_menu(EXPORT_XML)
    _drive_panel(_dialog(), str(destination.parent), destination.name, "Save")
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


def _dialog(timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        name = _osascript(DIALOG)
        if name:
            return name
        time.sleep(0.3)
    raise AppError("APP_DIALOG_MISSING", "rekordbox did not open its file dialog.")


def _drive_panel(window: str, path: str, file_name: str, confirm: str) -> None:
    outcome = _osascript(PANEL, window, path, file_name, confirm, timeout=60)
    if outcome != "done":
        _cancel(window)
        raise AppError(
            "APP_DIALOG_FAILED",
            f"Could not complete rekordbox's “{window}” dialog ({outcome}); nothing was typed "
            "into other apps. Keep rekordbox on screen and retry.",
        )


def _cancel(window: str) -> None:
    script = (
        'tell application "System Events" to tell process "rekordbox" to '
        f'click button "Cancel" of splitter group 1 of window "{window}"'
    )
    with contextlib.suppress(AppError):
        _osascript(script)
