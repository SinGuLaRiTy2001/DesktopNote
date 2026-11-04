"""UI strings in Simplified Chinese and English.

``tr(key, **fields)`` returns the string for the current language. Widgets set their texts in a
``retranslate()`` method, so switching language takes effect without recreating windows.
"""
from __future__ import annotations

from datetime import datetime

LANGUAGES = {"zh": "简体中文", "en": "English"}
DEFAULT_LANGUAGE = "zh"

_current = DEFAULT_LANGUAGE

_STRINGS: dict[str, tuple[str, str]] = {
    # Tray menu.
    "tray.new": ("新建记事板", "New Note"),
    "tray.show_all": ("显示全部记事板", "Show All Notes"),
    "tray.untitled": ("空白记事板", "Empty Note"),
    "tray.settings": ("设置", "Settings"),
    "tray.about": ("关于 DesktopNote", "About DesktopNote"),
    "tray.quit": ("退出", "Quit"),
    # Updates, in the About window.
    "update.check": ("检查更新", "Check for Updates"),
    "update.checking": ("正在检查…", "Checking…"),
    "update.current": ("已是最新版本", "Up to Date"),
    "update.install": ("更新到 {version}", "Update to {version}"),
    "update.download": ("下载 {version}", "Download {version}"),
    "update.installing": ("正在更新…", "Updating…"),
    "update.retry": ("重试", "Try Again"),  # After a failed check or update; the tooltip says why.
    # About window.
    "about.version": ("版本 {version}", "Version {version}"),
    "about.developer": ("由文简使用 Claude Code 开发", "Developed by Jian Wen with Claude Code"),
    "about.close": ("关闭", "Close"),
    "settings.theme_auto": ("跟随系统", "Match System"),
    "settings.theme_auto_short": ("自动", "Auto"),
    "settings.theme_light": ("浅色", "Light"),
    "settings.theme_dark": ("深色", "Dark"),
    "settings.autostart": ("开机自动启动", "Start at Login"),
    "settings.text_size": ("文字大小", "Text Size"),
    "settings.data_folder": ("打开数据文件夹", "Open Data Folder"),
    # Note window.
    "note.placeholder_view": ("双击或点按 {icon} 开始记录", "Double-click or tap {icon} to start"),
    "note.placeholder_edit": ("从下方添加文本、待办或提醒", "Add text, a to-do or a reminder below"),
    "note.add_text": ("添加文本", "Add Text"),
    "note.add_todo": ("添加待办事项", "Add To-do"),
    "note.add_reminder": ("添加提醒事项", "Add Reminder"),
    "note.edit": ("编辑", "Edit"),
    "note.done": ("完成", "Done"),
    "note.more": ("更多", "More"),
    "note.confirm_delete": ("删除此记事板？", "Delete this note?"),
    "note.cancel": ("取消", "Cancel"),
    "note.delete": ("删除", "Delete"),
    "note.title": ("标题", "Title"),
    "note.drag": ("拖动以移动", "Drag to move"),
    "menu.new": ("新建记事板", "New Note"),
    "menu.pin": ("置于顶层", "Keep on Top"),
    "menu.unpin": ("取消置顶", "Don't Keep on Top"),
    "menu.delete": ("删除记事板", "Delete Note"),
    # Items.
    "item.text": ("输入文本…", "Text…"),
    "item.todo": ("待办事项", "To-do"),
    "item.reminder": ("提醒事项", "Reminder"),
    "item.delete": ("删除此项", "Delete Item"),
    "item.mark_done": ("标记为完成", "Mark as Done"),
    "item.mark_undone": ("标记为未完成", "Mark as Not Done"),
    "item.add_time": ("添加时间", "Add Time"),
    "item.due_in": (" · {delta}后", " · in {delta}"),
    "item.overdue": (" · 已超时", " · Overdue"),
    "reminder.heading": ("提醒事项", "Reminder"),
    "reminder.fallback": ("提醒", "Reminder"),
    # Data folder problems, shown as tray messages.
    "data.restored": ("无法读取记事板，已从备份恢复。原文件保留为 {names}。",
                      "Your notes could not be read and were restored from the backup. The file was kept as {names}."),
    "data.unreadable": ("无法读取记事板。文件已保留在数据文件夹中：{names}。",
                        "Your notes could not be read. The files were kept in the data folder: {names}."),
    "data.save_failed": ("无法保存记事板，将继续重试：{error}",
                         "Your notes cannot be saved; retrying: {error}"),
    # Date and time.
    "date.today": ("今天 {hm}", "Today {hm}"),
    "date.tomorrow": ("明天 {hm}", "Tomorrow {hm}"),
    "date.yesterday": ("昨天 {hm}", "Yesterday {hm}"),
    "date.this_year": ("{m}月{d}日 {hm}", "{mon} {d}, {hm}"),
    "date.other_year": ("{y}年{m}月{d}日 {hm}", "{mon} {d}, {y}, {hm}"),
    "date.month": ("{y}年{m}月", "{month} {y}"),
    "date.weekdays": ("一 二 三 四 五 六 日", "Mo Tu We Th Fr Sa Su"),
    "delta.minutes": ("{n} 分钟", "{n} min"),
    "delta.hours": ("{n} 小时", "{n} h"),
    "delta.days": ("{n} 天", "{n} d"),
    "popup.time": ("时间", "Time"),
    "popup.clear": ("移除时间", "Remove Time"),
    "popup.done": ("完成", "Done"),
    # First-run welcome note.
    "welcome.1": ("欢迎使用 DesktopNote ✨\n拖动顶部的小横条即可移动，拖动边缘调整大小。",
                  "Welcome to DesktopNote ✨\nDrag the bar at the top to move, drag an edge to resize."),
    "welcome.2": ("点按左侧圆圈完成待办", "Tap the circle to complete a to-do"),
    "welcome.3": ("点按右下角的铅笔按钮进入编辑，用底部按钮添加内容",
                  "Tap the pencil at the bottom right to edit; add content with the buttons below"),
    "welcome.4": ("试试提醒事项", "Try a reminder"),
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS_LONG = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")


def set_language(code: str) -> None:
    """Sets the current language, falling back to the default for unknown codes."""
    global _current
    _current = code if code in LANGUAGES else DEFAULT_LANGUAGE


def language() -> str:
    """Returns the code of the current language."""
    return _current


def tr(key: str, **fields) -> str:
    """Returns the string for ``key`` in the current language.

    Args:
        key: Key into the string table, e.g. "tray.new".
        **fields: Values substituted into the string's ``{name}`` placeholders.

    Raises:
        KeyError: If ``key`` is unknown, or fields are given but a placeholder has no
            matching field.
    """
    zh, en = _STRINGS[key]
    text = en if _current == "en" else zh
    return text.format(**fields) if fields else text


def month_fields(d: datetime) -> dict:
    """Returns the placeholder fields for the ``date.*`` templates.

    Includes short and long month names, which the English templates need.
    """
    return {"y": d.year, "m": d.month, "d": d.day, "mon": _MONTHS[d.month - 1], "month": _MONTHS_LONG[d.month - 1]}
