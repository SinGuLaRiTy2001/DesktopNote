"""Builds the DesktopNote release: a folder app, zipped, with a SHA-256 checksum.

    python scripts/build.py           # -> dist/DesktopNote-<version>-win64.zip
    python scripts/build.py --notes   # Prints this version's CHANGELOG.md section.

PyInstaller packs the app in one-folder mode: it starts faster than a one-file executable and
keeps the LGPL-licensed Qt libraries replaceable. Qt modules and plugins the app does not use are
left out, and DesktopNote.exe gets the app icon and version information. The command line for
scripts and agents, desktopnote-cli.exe, is a small console executable next to it that shares its
libraries.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / "DesktopNote"
CLI_NAME = "desktopnote-cli"

USED_QT = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg"}
CLI_QT = {"QtCore", "QtNetwork"}  # The command line opens no windows.
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
RELEASE_FILES = ["LICENSE", "THIRD_PARTY_NOTICES.md", "licenses"]


def version() -> str:
    """Returns the package version from desktopnote/__init__.py."""
    text = (ROOT / "desktopnote" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def write_icon(path: Path) -> None:
    """Writes the app icon as a multi-size .ico with PNG-compressed images."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    from desktopnote import icons

    path.write_bytes(icons.app_ico())


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


def run_pyinstaller(icon: Path, version_file: Path, script: Path, name: str, windowed: bool,
                    used_qt: set[str], dist: Path) -> None:
    """Runs PyInstaller in one-folder mode with the unused modules excluded.

    Args:
        icon: The executable's icon.
        version_file: The version resource.
        script: The entry script.
        name: The executable's name, without ".exe".
        windowed: Whether it is a windowed program (no console).
        used_qt: The Qt modules it uses; the others are left out.
        dist: Where the folder goes.
    """
    import PySide6

    unused_qt = sorted(p.stem for p in Path(PySide6.__file__).parent.glob("Qt*.pyd") if p.stem not in used_qt)
    excludes = [f"PySide6.{module}" for module in unused_qt] + UNUSED_STDLIB
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--windowed" if windowed else "--console",
               "--name", name, "--icon", str(icon), "--version-file", str(version_file),
               "--distpath", str(dist), "--workpath", str(BUILD / "pyinstaller" / name), "--specpath", str(BUILD),
               "--log-level", "WARN"]
    for module in excludes:
        command += ["--exclude-module", module]
    subprocess.run(command + [str(script)], check=True, cwd=ROOT)


def add_cli(icon: Path, version_file: Path) -> None:
    """Builds desktopnote-cli.exe and puts it next to DesktopNote.exe, sharing its libraries.

    Only the executable, and any file the app does not already ship, is copied into the app
    folder; both were built from the same Python and PyInstaller, so the shared files match.
    """
    dist = BUILD / "cli-dist"
    shutil.rmtree(dist, ignore_errors=True)
    run_pyinstaller(icon, version_file, ROOT / "DesktopNoteCLI.py", CLI_NAME, False, CLI_QT, dist)
    shutil.copy2(dist / CLI_NAME / f"{CLI_NAME}.exe", APP)
    source = dist / CLI_NAME / "_internal"
    for path in sorted(source.rglob("*")):
        target = APP / "_internal" / path.relative_to(source)
        if path.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


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
    # LF line ending, as sha256sum -c expects.
    (DIST / f"{archive.name}.sha256").write_bytes(f"{digest}  {archive.name}\n".encode("ascii"))
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
    run_pyinstaller(icon, version_file, ROOT / "DesktopNote.pyw", "DesktopNote", True, USED_QT, DIST)
    add_cli(icon, version_file)
    prune()  # After add_cli, so nothing it copies brings a pruned file back.
    add_release_files()
    archive = package(ver)
    print(f"DesktopNote {ver}: folder {folder_mb(APP):.1f} MB, zip {archive.stat().st_size / 2**20:.1f} MB")
    print(f"  {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
