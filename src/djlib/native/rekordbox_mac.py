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


def screen_locked() -> bool:
    """Whether the login session's screen is locked (no app can be driven then)."""
    try:
        output = subprocess.run(
            ["ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    marker = output.find("CGSSessionScreenIsLocked")
    return marker != -1 and "<true/>" in output[marker : marker + 80]


def wait_for_unlock(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while screen_locked() and time.monotonic() < deadline:
        time.sleep(3)


def ensure_supported() -> None:
    if sys.platform != "darwin":
        raise AppError(
            "APP_AUTOMATION_UNSUPPORTED",
            "Driving rekordbox is only implemented on macOS so far.",
        )
    if screen_locked():
        raise AppError(
            "APP_SCREEN_LOCKED",
            "Unlock your Mac first; macOS does not let apps be controlled while it is locked.",
            retryable=True,
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


def _menus_ready() -> bool:
    script = (
        f'tell application "System Events" to tell process "{PROCESS}" to '
        'exists menu bar item "File" of menu bar 1'
    )
    try:
        return _osascript(script) == "true"
    except AppError:
        return False


def ensure_running(timeout: float = 120) -> None:
    """Launch rekordbox if needed and wait until its menus respond (no window required)."""
    launched = False
    if not _running():
        for name in APP_NAMES:
            if subprocess.run(["open", "-g", "-a", name], capture_output=True).returncode == 0:
                launched = True
                break
        else:
            raise AppError("APP_NOT_INSTALLED", "rekordbox is not installed in /Applications.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _running() and _menus_ready():
            if launched:
                time.sleep(5)  # let the library finish loading before importing
            return
        time.sleep(1)
    raise AppError("APP_NOT_READY", "rekordbox did not finish starting in time.")


def click_menu(name: str) -> None:
    outcome = _osascript(FIND_AND_CLICK, name)
    if outcome == "missing":
        raise AppError("APP_MENU_MISSING", f"rekordbox has no “{name}” menu item.")
    if outcome == "disabled":
        raise AppError(
            "APP_MENU_DISABLED",
            f"“{name}” is unavailable; close any open rekordbox dialog and retry.",
            retryable=True,
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


PLAYLISTS = """
on collect(theMenu)
  set out to {}
  tell application "System Events"
    repeat with itm in menu items of theMenu
      set n to name of itm
      if n is not missing value then
        set end of out to n
        try
          set out to out & my collect(menu 1 of itm)
        end try
      end if
    end repeat
  end tell
  return out
end collect

tell application "System Events"
  tell process "rekordbox"
    set trackMenu to menu 1 of menu bar item "Track" of menu bar 1
    set names to my collect(menu 1 of menu item "Add To Playlist" of trackMenu)
  end tell
end tell
set AppleScript's text item delimiters to linefeed
return names as text
"""


def playlists() -> set[str] | None:
    """Playlist names from rekordbox's Track menu, read without focusing rekordbox.

    None when rekordbox is not running or the menu is unavailable (it only exists while a
    track is selected); callers must then confirm through an XML export instead.
    """
    if not _running():
        return None
    try:
        return {line for line in _osascript(PLAYLISTS).splitlines() if line}
    except AppError:
        return None


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input."""
    try:
        output = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    for line in output.splitlines():
        if "HIDIdleTime" in line:
            try:
                return int(line.rsplit("=", 1)[-1].strip()) / 1_000_000_000
            except ValueError:
                return 0.0
    return 0.0


def wait_until_idle(seconds: float, timeout: float = 4 * 3600) -> None:
    """Block until nobody has touched the keyboard or mouse for ``seconds`` while unlocked.

    A locked screen counts as not ready: rekordbox cannot be driven until it is unlocked.
    """
    deadline = time.monotonic() + timeout
    while idle_seconds() < seconds or screen_locked():
        if time.monotonic() >= deadline:
            raise AppError("APP_IDLE_TIMEOUT", "The computer never became idle; nothing changed.")
        time.sleep(5)


CLICK_PATH = """
on run argv
  tell application "System Events"
    tell process "rekordbox"
      set target to menu bar item (item 1 of argv) of menu bar 1
      repeat with i from 2 to count of argv
        set target to menu item (item i of argv) of menu 1 of target
      end repeat
      if enabled of target is false then return "disabled"
      click target
      return "clicked"
    end tell
  end tell
end run
"""

MENU_ENABLED = """
on run argv
  tell application "System Events"
    tell process "rekordbox"
      set target to menu bar item (item 1 of argv) of menu bar 1
      repeat with i from 2 to count of argv
        set target to menu item (item i of argv) of menu 1 of target
      end repeat
      return enabled of target as text
    end tell
  end tell
end run
"""

EXPORT_TO_FILE = (
    "Playlist",
    "Export a playlist to a file",
    "Export a playlist to a file for music apps (*.m3u8)",
)


def click_menu_path(*path: str) -> None:
    """Click one menu item by its full path, e.g. Playlist > Export Playlist > RICARDO_AM."""
    try:
        outcome = _osascript(CLICK_PATH, *path)
    except AppError as exc:
        if exc.code == "APP_AUTOMATION_NOT_ALLOWED":
            raise
        raise AppError(
            "APP_MENU_MISSING", f"rekordbox has no “{' > '.join(path)}”.", retryable=True
        ) from exc
    if outcome == "disabled":
        raise AppError(
            "APP_MENU_DISABLED", f"“{' > '.join(path)}” is unavailable right now.", retryable=True
        )


def menu_state(*path: str) -> str:
    """'enabled', 'disabled' or 'missing' for one menu item path."""
    try:
        return "enabled" if _osascript(MENU_ENABLED, *path) == "true" else "disabled"
    except AppError as exc:
        if exc.code == "APP_AUTOMATION_NOT_ALLOWED":
            raise
        return "missing"


def menu_enabled(*path: str) -> bool:
    return menu_state(*path) == "enabled"


def selected_playlist() -> str | None:
    """Name of the playlist selected in rekordbox's browser, or None.

    rekordbox's browser is not exposed to accessibility, but its “export a playlist to a
    file” dialog is pre-filled with the selected playlist's name; it is read and cancelled.
    The menu can change between looking and clicking while the user clicks around, so a
    disabled item means nothing is selected right now, not an error.
    """
    try:
        click_menu_path(*EXPORT_TO_FILE)
    except AppError as exc:
        if exc.code == "APP_MENU_DISABLED":
            return None
        raise
    window = _dialog()
    script = (
        'tell application "System Events" to tell process "rekordbox" to get value of '
        f'text field "Save As:" of splitter group 1 of window "{window}"'
    )
    try:
        name = _osascript(script)
    finally:
        _cancel(window)
    return name.removesuffix(".m3u8").removesuffix(".M3U8") or None


FRONTMOST = 'tell application "System Events" to get frontmost of process "rekordbox"'
BRING_TO_FRONT = 'tell application "System Events" to set frontmost of process "rekordbox" to true'
NOTIFY = """
on run argv
  display notification (item 1 of argv) with title (item 2 of argv)
end run
"""


def frontmost() -> bool:
    try:
        return _osascript(FRONTMOST) == "true"
    except AppError:
        return False


def bring_to_front() -> None:
    """Show rekordbox so the user can click in it (best effort)."""
    with contextlib.suppress(AppError):
        _osascript(BRING_TO_FRONT)


def notify(message: str, title: str = "djlib") -> None:
    """A macOS notification, for a user who is looking at rekordbox rather than the terminal."""
    with contextlib.suppress(AppError):
        _osascript(NOTIFY, message, title)


def same_name(a: str | None, b: str | None) -> bool:
    """Playlist names compare equal however their accents were composed."""
    import unicodedata

    if a is None or b is None:
        return a is b
    return unicodedata.normalize("NFC", a) == unicodedata.normalize("NFC", b)


MIN_REPROBE_SECONDS = 2.0
# Look at the selection only once the user has paused this long, never mid-click.
SETTLE_SECONDS = 0.6
STATE_EVERY_SECONDS = 60.0
NOTIFY_EVERY_SECONDS = 180.0
# Menu hiccups while the user clicks around rekordbox; worth waiting through.
TRANSIENT = frozenset(
    {"APP_MENU_DISABLED", "APP_MENU_MISSING", "APP_DIALOG_MISSING", "APP_AUTOMATION_FAILED"}
)


def waiting_reason(state: str, device: str, selected: str | None = None) -> str:
    """Why the export has not started yet, in words the user can act on."""
    return {
        "starting": "waiting for rekordbox",
        "mac_locked": "your Mac is locked",
        "rekordbox_not_in_front": "rekordbox isn't the app in front",
        "stick_not_in_rekordbox": f"rekordbox doesn't list {device} under Playlist > Export "
        "Playlist; is the stick plugged in and shown under Devices in rekordbox?",
        "no_playlist_selected": "no playlist is selected in rekordbox",
        "wrong_playlist": f"“{selected or 'something else'}” is selected instead",
        "busy": "rekordbox's menus are busy; a dialog may be open",
    }.get(state, state)


def wait_for_selection(
    device: str,
    target: str,
    timeout: float,
    on_wrong=None,
    on_state=None,
    before_export=None,
) -> None:
    """Wait until the user selects ``target`` in rekordbox, then export it to ``device``.

    rekordbox's browser cannot be driven, so the user clicks the playlist; rekordbox is
    brought to the front and a notification says what to click. As soon as rekordbox enables
    Playlist > Export Playlist > device while it is in front, the selected playlist's name is
    read from the export-to-file dialog; only the requested playlist is exported, through
    rekordbox's own menu. Menu hiccups while the user clicks around are waited through.

    ``on_state(state, seconds_left, selected)`` hears why it is still waiting, on each change
    and every minute; ``before_export()`` runs just before the export is clicked.
    """
    instruction = f"Click “{target}” in rekordbox's playlist list to put it on {device}."
    bring_to_front()
    notify(instruction)
    started = time.monotonic()
    deadline = started + timeout
    last_wrong, probed_at = None, None
    state, reported, reported_at, notified_at = "starting", None, started, started

    def report(new_state: str) -> None:
        nonlocal state, reported, reported_at
        state = new_state
        now = time.monotonic()
        if on_state is not None and (state != reported or now - reported_at >= STATE_EVERY_SECONDS):
            on_state(state, max(0, round(deadline - now)), last_wrong)
            reported, reported_at = state, now

    while time.monotonic() < deadline:
        if time.monotonic() - notified_at >= NOTIFY_EVERY_SECONDS:
            notify(instruction)
            notified_at = time.monotonic()
        if screen_locked():
            report("mac_locked")
            time.sleep(0.4)
            continue
        if not frontmost():
            report("rekordbox_not_in_front")
            time.sleep(0.4)
            continue
        menu = menu_state("Playlist", "Export Playlist", device)
        if menu != "enabled":
            report("stick_not_in_rekordbox" if menu == "missing" else "no_playlist_selected")
            time.sleep(0.4)
            continue
        # Reading the selection briefly opens a dialog. After a wrong answer, look again only
        # once the user has clicked or typed since (djlib's own menu actions are not input),
        # so browsing other playlists is not interrupted.
        idle = idle_seconds()
        if probed_at is not None:
            since = time.monotonic() - probed_at
            if since < MIN_REPROBE_SECONDS or idle >= since:
                time.sleep(0.4)
                continue
        if idle < SETTLE_SECONDS:
            time.sleep(0.2)
            continue
        try:
            chosen = selected_playlist()
            probed_at = time.monotonic()
            if same_name(chosen, target):
                if before_export is not None:
                    before_export()
                click_menu_path("Playlist", "Export Playlist", device)
                return
        except AppError as exc:
            if exc.code not in TRANSIENT:
                raise
            probed_at = None  # look again as soon as rekordbox settles
            report("busy")
            time.sleep(1.0)
            continue
        if chosen is None:
            report("no_playlist_selected")
            continue
        if chosen != last_wrong:
            last_wrong = chosen
            if on_wrong is not None:
                on_wrong(chosen)
            notify(f"That's “{chosen}”. {instruction}")
        report("wrong_playlist")
    raise AppError(
        "APP_SELECTION_TIMEOUT",
        f"“{target}” was not selected in rekordbox in time "
        f"({waiting_reason(state, device, last_wrong)}); nothing was exported.",
        retryable=True,
        details={"last_state": state, "selected": last_wrong},
    )


def installed() -> dict | None:
    """The installed rekordbox app and its version, without launching it."""
    import plistlib

    for app in sorted(Path("/Applications").glob("rekordbox*/rekordbox.app"), reverse=True):
        try:
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        except (OSError, ValueError):
            info = {}
        return {"path": str(app), "version": info.get("CFBundleShortVersionString")}
    return None


def automation_allowed() -> bool | None:
    """Whether macOS lets this terminal drive other apps (None off macOS)."""
    if sys.platform != "darwin":
        return None
    try:
        return _osascript('tell application "System Events" to get UI elements enabled') == "true"
    except AppError:
        return False
