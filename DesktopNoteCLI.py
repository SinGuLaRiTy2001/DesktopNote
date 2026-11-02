"""Console launcher of the command line (desktopnote/cli.py); the release builds it as desktopnote-cli.exe."""
import os
import sys

# Makes the package importable regardless of the working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from desktopnote.cli import main  # noqa: E402

sys.exit(main())
