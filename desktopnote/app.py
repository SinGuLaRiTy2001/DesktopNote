"""Application controller: owns the store, note windows, tray icon and timers."""
from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from ctypes import wintypes
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QFont, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidgetAction

from . import __version__, commands, i18n, icons, motion, win_effects
from .about import AboutWindow
from .i18n import tr
from .instance import COMMAND_PREFIX, SERVER_NAME, claim
from .models import Item, Note
from .note_window import GlassMenu, NoteWindow, glass_menu
from .storage import Store, data_dir
from .theme import DARK, FONT_FAMILIES, LIGHT, Theme, menu_qss, nearest_text_scale, tag_color
from .updates import UpdateFlow
from .widgets import GlassToolTip, SegmentedControl, TextSizeSlider, default_due, reminder_state

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 400
SAVE_RETRY_MS = 5_000  # After a failed save.
SAVE_WARN_AFTER = 3  # Failed saves in a row before the tray says so.
LABEL_CHARS = 32  # Tray-list entries are cut to this many characters.
LOG_FILE = "desktopnote.log"  # In the data folder; only warnings and errors are written.
LOG_BYTES = 256 * 1024  # The log is rotated at this size, keeping one older file.
TOAST_CHECK_MS = 10_000  # After this, a notification that failed is shown as a tray message.

# Windows messages and power-broadcast events handled by _SystemEventFilter.
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007


class _SystemEventFilter(QAbstractNativeEventFilter):
    """Flushes notes to disk before shutdown or sleep and refreshes the app after resume."""

    def __init__(self, app: "DesktopNoteApp"):
        super().__init__()
        self.app = app

    def nativeEventFilter(self, event_type, message):
        """Watches session-end and power messages; never consumes them."""
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (WM_QUERYENDSESSION, WM_ENDSESSION):
                self.app.save_now()
            elif msg.message == WM_POWERBROADCAST:
                if msg.wParam == PBT_APMSUSPEND:
                    self.app.save_now()
                elif msg.wParam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                    QTimer.singleShot(1500, self.app.after_resume)
        return False, 0


class DesktopNoteApp:
    """Owns the note store, one NoteWindow per note, the tray icon and its menu, and the timers.

    Saving is debounced (SAVE_DELAY_MS) and forced on quit, session end and sleep. Reminders are
    checked every 20 seconds.

    Attributes:
        qapp: The running QApplication.
        store: Notes and settings, loaded from disk.
        windows: Open note windows by note id.
        about: The last About window opened, or None; see _about_open().
        theme: The current Theme (light or dark).
        save_timer: Single-shot timer behind schedule_save.
        reminder_timer: Periodic timer that runs check_reminders.
        tray: The system tray icon.
        tray_menu: The tray icon's context menu, rebuilt each time it opens.
    """

    NEW_NOTE_W, NEW_NOTE_H = commands.NEW_NOTE_W, commands.NEW_NOTE_H

    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        self.store = Store()
        try:
            with self.store.locked():
                self.store.load()
        except OSError as exc:  # The lock could not be taken.
            log.warning("data folder lock: %s", exc)
            self.store.load()
        settings = self.store.settings
        settings.text_scale = nearest_text_scale(settings.text_scale)  # The steps may have changed since it was saved.
        self.windows: dict[str, NoteWindow] = {}
        self.about: AboutWindow | None = None
        i18n.set_language(self.store.settings.language)
        self._apply_color_scheme()
        self.theme = self._detect_theme()
        GlassToolTip.install(qapp, self.theme)

        self._wanted_scale: float | None = None
        self._scale_timer = QTimer(singleShot=True, interval=0)
        self._scale_timer.timeout.connect(self._apply_wanted_scale)

        self.save_timer = QTimer(singleShot=True)
        self.save_timer.timeout.connect(self.save_now)
        self._save_failures = 0

        self.reminder_timer = QTimer(interval=20_000)
        self.reminder_timer.timeout.connect(self.check_reminders)

        self.updates = UpdateFlow(__version__, data_dir())  # Shown in the About window.
        self.updates.quit_requested.connect(self.quit)  # The update installs once the app has quit.

        self._native_filter = _SystemEventFilter(self)
        qapp.installNativeEventFilter(self._native_filter)
        qapp.aboutToQuit.connect(self.save_now)
        qapp.commitDataRequest.connect(lambda _manager: self.save_now())
        qapp.styleHints().colorSchemeChanged.connect(self._scheme_changed)
        self._fading = False
        # True while this class calls setColorScheme itself; its caller rethemes, so the
        # resulting colorSchemeChanged signal is ignored.
        self._setting_scheme = False
        # A monitor was unplugged or the layout changed while running: pull stranded notes back
        # once Windows has settled the new layout.
        qapp.screenRemoved.connect(lambda _screen: QTimer.singleShot(500, self._rescue_all))

        self._init_tray()

    # --- Start ---------------------------------------------------------------

    def start(self) -> None:
        """Opens a window for every note and starts the reminder checks.

        On the first run it also registers autostart and adds a welcome note when there are no
        notes yet. It then reports data files that could not be read.
        """
        settings = self.store.settings
        if settings.first_run:
            settings.first_run = False
            try:
                win_effects.set_autostart(settings.autostart)
            except OSError as exc:
                log.warning("autostart: %s", exc)
            if not self.store.notes:
                self.store.notes.append(self._welcome_note())
            self.save_now()
        elif settings.autostart and win_effects.autostart_entry() != win_effects.autostart_command():
            # Keeps the Run entry pointing at the current install, e.g. after the folder moved.
            try:
                win_effects.set_autostart(True)
            except OSError as exc:
                log.warning("autostart: %s", exc)
        # One ghost per note plus the tray menus, so theme cross-fades start at once.
        motion.Ghost.prewarm(len(self.store.notes) + 3)
        for note in self.store.notes:
            self._open_window(note)
        self.check_reminders()
        self.reminder_timer.start()
        self._report_unreadable()

    def _report_unreadable(self) -> None:
        """Tells the user, from the tray, about data files that could not be read."""
        names = ", ".join(path.name for path in self.store.set_aside)
        if self.store.from_backup:
            self._tray_message("DesktopNote", tr("data.restored", names=names))
        elif names:
            self._tray_message("DesktopNote", tr("data.unreadable", names=names))

    def _welcome_note(self) -> Note:
        """Builds the note shown on the first run, near the top right of the primary screen."""
        screen = QGuiApplication.primaryScreen().availableGeometry()
        note = Note(x=screen.right() - 360, y=screen.top() + 80, w=310, h=360, tint="lemon")
        note.items = [
            Item(type="text", text=tr("welcome.1")),
            Item(type="todo", text=tr("welcome.2")),
            Item(type="todo", text=tr("welcome.3"), done=False),
            Item(type="reminder", text=tr("welcome.4"), due=default_due().isoformat(timespec="minutes")),
        ]
        return note

    # --- Windows -------------------------------------------------------------

    def _open_window(self, note: Note) -> NoteWindow:
        """Creates, connects and shows the window of a note."""
        self._ensure_on_screen(note)
        settings = self.store.settings
        win = NoteWindow(note, self.theme, settings.due_soon_minutes, settings.text_scale)
        win.setWindowIcon(icons.app_icon())
        win.changed.connect(self.schedule_save)
        win.newNoteRequested.connect(lambda source: self.new_note(near=source))
        win.deleteRequested.connect(self.delete_note)
        self.windows[note.id] = win
        win.dock.restore()
        win.show_animated()
        return win

    @staticmethod
    def _ensure_on_screen(note: Note) -> None:
        """Moves a note from a disconnected monitor back onto the primary screen.

        A note counts as visible when at least 60 x 40 px of it lie on some screen; otherwise it
        is centred on the primary screen and shrunk to fit if needed.
        """
        rect = QRect(note.x, note.y, note.w, note.h)
        for screen in QGuiApplication.screens():
            visible = screen.availableGeometry().intersected(rect)
            if visible.width() >= 60 and visible.height() >= 40:
                return
        area = QGuiApplication.primaryScreen().availableGeometry()
        note.w = min(note.w, area.width())
        note.h = min(note.h, area.height())
        note.x = area.center().x() - note.w // 2
        note.y = area.center().y() - note.h // 2

    def new_note(self, near: NoteWindow | None = None) -> NoteWindow:
        """Creates a note with an empty text row, opens it and focuses it.

        Args:
            near: A window to cascade from. A note docked at a screen edge opens it beside itself
                instead, where it is not covered. Without it, the note is centred on the screen
                under the cursor, offset so that successive notes do not stack exactly.

        Every new note starts the same: default size, no tag, no title.

        Returns:
            The new note's window.
        """
        note = Note(w=self.NEW_NOTE_W, h=self.NEW_NOTE_H)
        if near is not None:
            src = near.note  # Where it shows, even while hidden at an edge.
            note.x, note.y = src.x + 32, src.y + 32
            beside = near.dock.place_beside(note.w, note.h)
            if beside is not None:
                note.x, note.y = beside.x(), beside.y()
        else:
            self._centre(note)
        self.store.notes.append(note)
        win = self._open_window(note)
        win.bring_to_front()
        win.activateWindow()
        win.add_item("text")
        self.schedule_save()
        return win

    def _centre(self, note: Note) -> None:
        """Centres a new note on the screen under the cursor, offset from earlier new notes."""
        area = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        note.x = area.center().x() - note.w // 2 + 24 * (len(self.windows) % 6)
        note.y = area.center().y() - note.h // 2 + 24 * (len(self.windows) % 6)

    def handle_command(self, request: dict) -> dict:
        """Carries out a command-line request (see cli.py) on the open notes and saves.

        A new note opens like one made from the tray, an added item expands into its note, a
        changed item updates in its window, and a deleted item fades out of it.

        Args:
            request: The request, as commands.run() takes it.

        Returns:
            The JSON-ready result.
        """
        try:
            result, change = commands.run(request, self.store.notes)
        except commands.CommandError as exc:
            return {"ok": False, "error": str(exc)}
        if change is None:
            return result
        if change.new_note:
            self._centre(change.note)
            self._open_window(change.note)
        elif (win := self.windows.get(change.note.id)) is not None:
            if change.action == "add":
                win.show_new_item(change.item)
            elif change.action == "edit":
                win.refresh_item(change.item)
            elif change.action == "delete":
                win.forget_item(change.item)
        self.save_now()
        self.check_reminders()
        return result

    def delete_note(self, win: NoteWindow) -> None:
        """Removes a note, fades its window out and saves at once."""
        self.windows.pop(win.note.id, None)
        self.store.notes = [n for n in self.store.notes if n.id != win.note.id]
        motion.Ghost.from_screen(win, 8)  # The DWM corner radius.
        win.close()
        win.deleteLater()
        self.save_now()

    def _rescue(self, win: NoteWindow) -> None:
        """Brings a note on a monitor that has gone (or off a changed layout) back on screen."""
        if win.dock.validate():
            return
        note = win.note
        before = (note.x, note.y, note.w, note.h)
        self._ensure_on_screen(note)
        if (note.x, note.y, note.w, note.h) != before:
            win.setGeometry(note.x, note.y, note.w, note.h)

    def _rescue_all(self, *_) -> None:
        for win in self.windows.values():
            self._rescue(win)

    def show_all(self) -> None:
        """Brings every note to the front, or creates one when there are none.

        Notes hidden at an edge slide out for a while.
        """
        if not self.windows:
            self.new_note()
            return
        for win in self.windows.values():
            self._rescue(win)
            if win.dock.edge:
                win.dock.reveal(hold=True)
            else:
                win.bring_to_front()

    def focus_note(self, note_id: str) -> None:
        """Brings one note to the front, activates it and flashes it."""
        win = self.windows.get(note_id)
        if win is None:
            return
        self._rescue(win)
        if win.dock.edge:
            win.dock.reveal(hold=True)
        else:
            win.bring_to_front()
        win.activateWindow()
        win.flash()

    def retheme(self) -> None:
        """Re-detects the theme and applies it to the notes, tooltips and tray menu."""
        self.theme = self._detect_theme()
        GlassToolTip.theme = self.theme
        for win in self.windows.values():
            win.apply_theme(self.theme)
        if self._about_open():
            self.about.apply_theme(self.theme)
        self._refresh_tray_menu()

    def set_theme_mode(self, mode: str) -> None:
        """Sets the theme setting ("auto", "light" or "dark") and applies it."""
        self.store.settings.theme = mode
        self._apply_color_scheme()
        self.retheme()
        self.schedule_save()

    def set_language(self, code: str) -> None:
        """Sets the interface language and retranslates every note."""
        self.store.settings.language = code
        i18n.set_language(code)
        for win in self.windows.values():
            win.retranslate()
        self.schedule_save()

    def set_text_scale(self, scale: float) -> None:
        """Sets the text size step and applies it to every note."""
        self.store.settings.text_scale = scale
        for win in self.windows.values():
            win.set_text_scale(scale)
        self.schedule_save()

    def request_text_scale(self, scale: float) -> None:
        """Queues a text size from the slider, coalescing rapid requests.

        Clicks can come faster than all notes re-lay out, so only the latest request is applied
        (once the queued input has been handled), never a backlog of stale sizes.
        """
        self._wanted_scale = scale
        if not self._scale_timer.isActive():
            self._scale_timer.start()

    def _apply_wanted_scale(self) -> None:
        if self._wanted_scale is not None and self._wanted_scale != self.store.settings.text_scale:
            self.set_text_scale(self._wanted_scale)
        self._wanted_scale = None

    def _apply_color_scheme(self) -> None:
        """Forces Qt's own palette to the chosen mode too, which affects stock widgets."""
        hints = QGuiApplication.styleHints()
        mode = self.store.settings.theme
        self._setting_scheme = True
        try:
            if mode == "light":
                hints.setColorScheme(Qt.ColorScheme.Light)
            elif mode == "dark":
                hints.setColorScheme(Qt.ColorScheme.Dark)
            else:
                hints.unsetColorScheme()
        finally:
            self._setting_scheme = False

    def _scheme_changed(self, _scheme) -> None:
        """Cross-fades into the new appearance when Windows switches light/dark (theme "auto")."""
        if not self._setting_scheme:
            self._crossfade(self.retheme)

    def after_resume(self) -> None:
        """Restores the notes' glass and checks reminders after the computer wakes up."""
        for win in self.windows.values():
            win.apply_theme(self.theme)  # Re-applies the acrylic, which can drop after sleep.
        self.check_reminders()

    def _detect_theme(self) -> Theme:
        """Returns the theme for the setting, following Qt's or else Windows' scheme for "auto"."""
        mode = self.store.settings.theme
        if mode in ("light", "dark"):
            return DARK if mode == "dark" else LIGHT
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return DARK
        if scheme == Qt.ColorScheme.Light:
            return LIGHT
        return DARK if win_effects.system_uses_dark_theme() else LIGHT

    # --- Persistence ---------------------------------------------------------

    def schedule_save(self) -> None:
        """Saves after SAVE_DELAY_MS, restarting the delay on every call."""
        self.save_timer.start(SAVE_DELAY_MS)

    def save_now(self) -> None:
        """Saves immediately, cancelling any pending delayed save.

        A failed save is logged and retried after SAVE_RETRY_MS; after SAVE_WARN_AFTER failures in
        a row, a tray message says so.
        """
        self.save_timer.stop()
        try:
            self.store.save()
        except OSError as exc:
            log.error("Saving failed: %s", exc)
            self._save_failures += 1
            if self._save_failures == SAVE_WARN_AFTER:
                self._tray_message("DesktopNote", tr("data.save_failed", error=exc))
            self.save_timer.start(SAVE_RETRY_MS)
        else:
            self._save_failures = 0

    # --- Reminders -----------------------------------------------------------

    def check_reminders(self) -> None:
        """Refreshes reminder states and notifies once for each newly overdue reminder."""
        now = datetime.now()
        soon_minutes = self.store.settings.due_soon_minutes
        fired = False
        for win in self.windows.values():
            win.refresh_reminders(now)
            for item in win.note.items:
                if (item.type == "reminder" and not item.notified
                        and reminder_state(item, soon_minutes, now) == "overdue"):
                    item.notified = True
                    fired = True
                    heading = win.note.title.strip() or tr("reminder.heading")
                    text = item.text or tr("reminder.fallback")
                    self._notify(heading, text)
        if fired:
            self.schedule_save()

    def _notify(self, heading: str, text: str) -> None:
        """Shows a Windows notification, or a tray message if the notification fails."""
        proc = win_effects.show_toast(heading, text)
        if proc is None:
            self._tray_message(heading, text)
            return

        def check() -> None:
            if proc.poll() not in (None, 0):  # Exited with an error: no notification was shown.
                self._tray_message(heading, text)

        QTimer.singleShot(TOAST_CHECK_MS, check)

    def _tray_message(self, heading: str, text: str) -> None:
        """Shows a message from the tray icon."""
        self.tray.showMessage(heading, text, icons.app_icon(), 8000)

    # --- Tray ----------------------------------------------------------------

    def _init_tray(self) -> None:
        self.tray = QSystemTrayIcon(icons.app_icon())
        self.tray.setToolTip(f"DesktopNote {__version__}")
        self.tray_menu = glass_menu(self.theme, None)
        self.tray_menu.aboutToShow.connect(self._populate_tray_menu)
        self._populate_tray_menu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _populate_tray_menu(self) -> None:
        """Rebuilds the tray menu; this runs on every open because the note list changes.

        Settings toggles keep the menu open and refresh it in place: switches, language and
        theme update live.
        """
        menu, settings = self.tray_menu, self.store.settings
        if self._about_open():  # The menu can switch the language, which About does not follow.
            self.about.dismiss()
        for sub in menu.findChildren(QMenu, options=Qt.FindDirectChildrenOnly):
            sub.deleteLater()
        menu.clear()
        # Entries: (action or submenu, icon name or None, text(), switch state() or None).
        self._tray_items: list[tuple] = []

        def add(target, icon_name, text, slot, keep_open=False, switch=None):
            action = target.addAction(text(), slot)
            action.setProperty("keepOpen", keep_open)
            self._tray_items.append((action, icon_name, text, switch))
            return action

        def widget_row(target, widget):
            row = QWidgetAction(target)
            row.setDefaultWidget(widget)
            target.addAction(row)

        add(menu, "plus", lambda: tr("tray.new"), self.new_note)
        add(menu, "note", lambda: tr("tray.show_all"), self.show_all)
        menu.addSeparator()

        # Every note by tag dot and name: picking one brings it to the front.
        if self.store.notes:
            for note in self.store.notes:
                name = note.label() or tr("tray.untitled")
                if len(name) > LABEL_CHARS:
                    name = name[:LABEL_CHARS - 1].rstrip() + "…"
                action = add(menu, None, lambda n=note, short=name: short if n.label() else tr("tray.untitled"),
                             lambda _=False, note_id=note.id: self.focus_note(note_id))
                action.setData(note.tint)
            menu.addSeparator()

        sub = glass_menu(self.theme, menu)
        self._tray_items.append((sub, "settings", lambda: tr("tray.settings"), None))
        # Settings controls, all applied live with the menu staying open: segmented theme,
        # text size slider, segmented language, and a switch for Start at Login.
        self._tray_theme = SegmentedControl(self.theme, self._theme_options(), settings.theme)
        self._tray_theme.changed.connect(lambda mode: self._tray_setting(self.set_theme_mode, mode, fade=True))
        widget_row(sub, self._tray_theme)
        sub.addSeparator()
        self._tray_slider = TextSizeSlider(self.theme, settings.text_scale)
        self._tray_slider.changed.connect(self.request_text_scale)
        widget_row(sub, self._tray_slider)
        sub.addSeparator()
        self._tray_lang = SegmentedControl(self.theme, list(i18n.LANGUAGES.items()), i18n.language())
        self._tray_lang.changed.connect(lambda code: self._tray_setting(self.set_language, code, fade=True))
        widget_row(sub, self._tray_lang)
        sub.addSeparator()
        add(sub, "power", lambda: tr("settings.autostart"),
            lambda: self._tray_setting(self._toggle_autostart), keep_open=True,
            switch=win_effects.is_autostart_enabled)
        add(sub, "folder", lambda: tr("settings.data_folder"),
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        menu.addMenu(sub)

        menu.addSeparator()
        add(menu, "info", lambda: tr("tray.about"), self.show_about)
        add(menu, "quit", lambda: tr("tray.quit"), self.quit)
        self._refresh_tray_menu()

    @staticmethod
    def _theme_options() -> list[tuple[str, str]]:
        """Returns the (mode, label) pairs of the theme segmented control."""
        return [("auto", tr("settings.theme_auto_short")), ("light", tr("settings.theme_light")),
                ("dark", tr("settings.theme_dark"))]

    def _tray_setting(self, apply, *args, fade: bool = False) -> None:
        """Applies a setting from the tray menu, then refreshes the menu, optionally cross-faded."""
        def run() -> None:
            apply(*args)
            self._refresh_tray_menu()

        if fade:
            self._crossfade(run)
        else:
            run()

    def _crossfade(self, apply) -> None:
        """Runs apply while every visible note and open menu cross-fades (theme and language)."""
        if self._fading:  # For example, the colour-scheme signal fired by this app's own switch.
            apply()
            return
        self._fading = True
        targets = [(win, 8) for win in self.windows.values()]  # 8 is the notes' DWM corner radius.
        targets += [(menu, GlassMenu.RADIUS) for menu in (self.tray_menu, *self.tray_menu.findChildren(QMenu))]

        def run() -> None:
            try:
                apply()
            finally:
                self._fading = False

        motion.crossfade(targets, run)

    def _refresh_tray_menu(self) -> None:
        """Updates texts, switches, icons and colours of the (possibly open) tray menu."""
        qss = menu_qss(self.theme)
        self.tray_menu.setStyleSheet(qss)
        for control in (self._tray_theme, self._tray_slider, self._tray_lang):
            control.theme = self.theme
            control.update()
        self._tray_theme.set_options(self._theme_options())
        self._tray_theme.set_current(self.store.settings.theme)
        self._tray_lang.set_current(i18n.language())
        for menu in (self.tray_menu, *self.tray_menu.findChildren(QMenu)):
            menu.theme = self.theme
        for target, icon_name, text, switch in self._tray_items:
            if isinstance(target, QMenu):
                target.setStyleSheet(qss)
                target.setTitle(text())
            else:
                target.setText(text().replace("&", "&&"))
                if switch is not None:
                    target.parent().set_switch(target, switch())
            if icon_name is not None:
                target.setIcon(icons.menu_icon(icon_name, self.theme.text))
            else:  # A note entry: its icon is the tag dot.
                target.setIcon(icons.dot(tag_color(target.data()), self.theme.tertiary))
        for menu in (self.tray_menu, *self.tray_menu.findChildren(QMenu)):
            if menu.isVisible():
                menu.adjustSize()  # A new language can change the width.
                menu.update()  # Switches are painted by the menu itself.

    def show_about(self) -> None:
        """Opens the About window, or brings the open one to the front."""
        if self._about_open():
            self.about.raise_()
            self.about.activateWindow()
        else:
            self.about = AboutWindow(self.theme, self.updates)
            self.about.popup()

    def _about_open(self) -> bool:
        """Returns whether the About window is open and not already fading out."""
        return self.about is not None and not self.about.closing

    def _toggle_autostart(self) -> None:
        enabled = not win_effects.is_autostart_enabled()
        self.store.settings.autostart = enabled

        try:
            win_effects.set_autostart(enabled)
        except OSError as exc:
            log.warning("autostart: %s", exc)
        self.schedule_save()

    def _tray_activated(self, reason) -> None:
        """Shows all notes on a click or double-click of the tray icon."""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_all()

    def quit(self) -> None:
        """Saves, removes the tray icon and quits the application."""
        self.save_now()
        self.tray.hide()
        self.qapp.quit()


# --- Single instance ---------------------------------------------------------


def _setup_logging() -> None:
    """Logs warnings and errors to LOG_FILE, and everything to the console if there is one.

    A windowed app has no console, so unhandled exceptions, which Qt prints and survives, are
    logged as well.
    """
    handlers: list[logging.Handler] = []
    try:
        to_file = RotatingFileHandler(data_dir() / LOG_FILE, maxBytes=LOG_BYTES, backupCount=1,
                                      encoding="utf-8", delay=True)  # Created on the first message.
        to_file.setLevel(logging.WARNING)
        handlers.append(to_file)
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)

    def log_unhandled(kind, value, tb) -> None:
        logging.getLogger("desktopnote").error("Unhandled exception", exc_info=(kind, value, tb))

    sys.excepthook = log_unhandled


def answer(conn: QLocalSocket, handle_command: Callable[[dict], dict], show: Callable[[], None]) -> None:
    """Answers a connection to the instance's local server.

    A command-line request (cli.py: COMMAND_PREFIX, JSON, newline) gets a JSON reply line; any
    other message comes from a second launch and shows the notes.

    Args:
        conn: The connected QLocalSocket.
        handle_command: Called with a request dict; returns the reply dict.
        show: Called for a second launch.
    """
    data = b""
    while conn.waitForReadyRead(300):
        data += bytes(conn.readAll())
        if not data.startswith(COMMAND_PREFIX) or data.endswith(b"\n"):
            break
    if not data.startswith(COMMAND_PREFIX):
        show()
        return
    try:
        reply = handle_command(json.loads(data[len(COMMAND_PREFIX):]))
    except ValueError:
        reply = {"ok": False, "error": "unreadable request"}
    conn.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
    conn.flush()
    conn.waitForBytesWritten(1000)
    conn.disconnectFromServer()


def main() -> int:
    """Runs DesktopNote, or wakes the instance that is already running.

    Returns:
        The process exit code.
    """
    if not claim(SERVER_NAME):  # Before any window code: a second launch hands over quickly.
        return 0
    _setup_logging()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("DesktopNote")
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(icons.app_icon())
    # The name and icon Windows shows on the app's notifications.
    icon_file = data_dir() / "DesktopNote.ico"
    try:
        icon_file.write_bytes(icons.app_ico())
        win_effects.register_app_id(str(icon_file))
    except OSError as exc:
        log.warning("app id: %s", exc)
    # Menus get the app's own eased transitions (motion.py) instead of Qt's linear ones.
    qapp.setEffectEnabled(Qt.UI_AnimateMenu, False)
    qapp.setEffectEnabled(Qt.UI_FadeMenu, False)
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(10)
    qapp.setFont(font)

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    app = DesktopNoteApp(qapp)

    server.newConnection.connect(lambda: answer(server.nextPendingConnection(), app.handle_command, app.show_all))
    app.start()
    return qapp.exec()
