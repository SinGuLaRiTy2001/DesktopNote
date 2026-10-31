"""Entry point for ``python -m desktopnote``."""
import sys

from desktopnote.instance import claim

if not claim():  # Before the app loads, so a second launch hands over quickly.
    sys.exit(0)

from desktopnote.app import main  # noqa: E402

sys.exit(main())
