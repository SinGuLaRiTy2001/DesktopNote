# Third-party notices

DesktopNote is MIT-licensed (see [LICENSE](LICENSE)). The Windows release bundles:

| Component | License | Text |
|---|---|---|
| [Qt 6](https://www.qt.io/) and [PySide6 / Shiboken6](https://wiki.qt.io/Qt_for_Python) (The Qt Company) | LGPL-3.0 | [licenses/LGPL-3.0.txt](licenses/LGPL-3.0.txt), [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt) |
| [Python](https://www.python.org/) runtime (Python Software Foundation) | PSF License | [licenses/Python-PSF.txt](licenses/Python-PSF.txt) |
| [PyInstaller](https://pyinstaller.org/) bootloader | GPL-2.0 with the bootloader exception, which allows distributing the built app under any license | - |

The Qt and PySide6 libraries are unmodified and kept as separate files in the `_internal` folder, so
they can be replaced with other compatible builds. Their source code is available from
[download.qt.io](https://download.qt.io/official_releases/QtForPython/) and
[code.qt.io](https://code.qt.io/cgit/pyside/pyside-setup.git/).
