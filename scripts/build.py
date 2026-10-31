"""Builds the DesktopNote release: a folder app, zipped, with a SHA-256 checksum.

    python scripts/build.py           # -> dist/DesktopNote-<version>-win64.zip
    python scripts/build.py --notes   # Prints this version's CHANGELOG.md section.

PyInstaller packs the app in one-folder mode: it starts faster than a one-file executable and
keeps the LGPL-licensed Qt libraries replaceable. Qt modules and plugins the app does not use are
left out, and DesktopNote.exe gets the app icon and version information.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / "DesktopNote"

USED_QT = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg"}
# Standard-library modules the app never loads; ssl and hashlib would also bring in OpenSSL.
UNUSED_STDLIB = ["tkinter", "unittest", "pydoc", "doctest", "lib2to3", "sqlite3", "xmlrpc", "pdb", "ssl", "_ssl",
                 "hashlib", "_hashlib", "decimal", "_decimal", "unicodedata", "email", "http", "urllib", "xml",
                 "asyncio", "multiprocessing"]
# Files PyInstaller collects that the app never loads, relative to the _internal folder.
PRUNE = [
    "PySide6/opengl32sw.dll",  # Software OpenGL fallback; the app draws with the raster engine.
    "libcrypto-3-x64.dll", "libssl-3-x64.dll",  # OpenSSL for Qt's TLS plugin, removed below.
    "PySide6/translations",  # Qt's own translations; the app ships its own texts.
    "PySide6/plugins/generic", "PySide6/plugins/iconengines", "PySide6/plugins/imageformats",
    "PySide6/plugins/networkinformation", "PySide6/plugins/platforminputcontexts", "PySide6/plugins/tls",
    "PySide6/plugins/platforms/qoffscreen.dll", "PySide6/plugins/platforms/qminimal.dll",
    "PySide6/plugins/platforms/qdirect2d.dll",
]
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
RELEASE_FILES = ["LICENSE", "THIRD_PARTY_NOTICES.md", "licenses"]


def version() -> str:
    """Returns the package version from desktopnote/__init__.py."""
    text = (ROOT / "desktopnote" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def write_icon(path: Path) -> None:
    """Writes the app icon as a multi-size .ico with PNG-compressed images."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    from desktopnote import icons

    images = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        icons.pixmap("app", "#000000", size, 1.0).save(buffer, "PNG")
        images.append((size, bytes(buffer.data())))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        side = size if size < 256 else 0  # 0 stands for 256 in the ICO format.
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


def write_version_file(path: Path, ver: str) -> None:
    """Writes the PyInstaller version resource, which Explorer and Task Manager show."""
    numbers = (re.match(r"[\d.]+", ver).group().strip(".").split(".") + ["0"] * 4)[:4]  # 1.0.0rc1 -> 1.0.0.0
    tuple_text = ", ".join(numbers)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tuple_text}), prodvers=({tuple_text}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'DesktopNote'),
      StringStruct('FileDescription', 'DesktopNote'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'DesktopNote'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'DesktopNote.exe'),
      StringStruct('ProductName', 'DesktopNote'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def run_pyinstaller(icon: Path, version_file: Path) -> None:
    """Runs PyInstaller in one-folder, windowed mode with the unused modules excluded."""
    import PySide6

    unused_qt = sorted(p.stem for p in Path(PySide6.__file__).parent.glob("Qt*.pyd") if p.stem not in USED_QT)
    excludes = [f"PySide6.{name}" for name in unused_qt] + UNUSED_STDLIB
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
               "--name", "DesktopNote", "--icon", str(icon), "--version-file", str(version_file),
               "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
               "--log-level", "WARN"]
    for module in excludes:
        command += ["--exclude-module", module]
    subprocess.run(command + [str(ROOT / "DesktopNote.pyw")], check=True, cwd=ROOT)


def prune() -> None:
    """Removes the files listed in PRUNE from the built folder."""
    for rel in PRUNE:
        target = APP / "_internal" / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def add_release_files() -> None:
    """Copies the licence and third-party notices next to DesktopNote.exe."""
    for name in RELEASE_FILES:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, APP / name, dirs_exist_ok=True)
        else:
            shutil.copy2(source, APP / name)


def package(ver: str) -> Path:
    """Zips the app folder and writes its SHA-256 checksum next to the zip."""
    archive = DIST / f"DesktopNote-{ver}-win64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(APP.rglob("*")):
            if path.is_file():
                zf.write(path, Path("DesktopNote") / path.relative_to(APP))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (DIST / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def release_notes(ver: str) -> str:
    """Returns the CHANGELOG.md section of version ver, without its heading."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[{re.escape(ver)}\][^\n]*\n(.*?)(?=^## \[|^\[)", text, re.M | re.S)
    if match is None:
        raise SystemExit(f"CHANGELOG.md has no section for {ver}")
    return match.group(1).strip() + "\n"


def folder_mb(path: Path) -> float:
    """Returns the total size of the files under path in MB."""
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 2**20


def main() -> int:
    """Builds, prunes, packages and prints a size summary; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--notes", action="store_true", help="print the release notes and exit")
    ver = version()
    if parser.parse_args().notes:
        sys.stdout.write(release_notes(ver))
        return 0
    shutil.rmtree(DIST, ignore_errors=True)
    BUILD.mkdir(exist_ok=True)
    icon, version_file = BUILD / "DesktopNote.ico", BUILD / "version_info.txt"
    write_icon(icon)
    write_version_file(version_file, ver)
    run_pyinstaller(icon, version_file)
    prune()
    add_release_files()
    archive = package(ver)
    print(f"DesktopNote {ver}: folder {folder_mb(APP):.1f} MB, zip {archive.stat().st_size / 2**20:.1f} MB")
    print(f"  {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
