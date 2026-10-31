"""Console-less launcher, used when double-clicked and by the Windows autostart entry."""
import os
import sys

# Makes the package importable regardless of the working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from desktopnote.instance import claim  # noqa: E402

if not claim():  # Before the app loads, so a second launch hands over quickly.
    sys.exit(0)

from desktopnote.app import main  # noqa: E402

sys.exit(main())
