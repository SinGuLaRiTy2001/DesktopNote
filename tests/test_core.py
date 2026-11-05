"""App-wide tests: the string table, one instance per data folder, the version, and the release
build's module list.
"""
import ast
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote import i18n  # noqa: E402
from desktopnote.instance import claim  # noqa: E402
from tests import _support  # noqa: E402


class I18nTest(unittest.TestCase):
    """Every string exists in both languages; an unknown language code falls back to Chinese."""

    def test_every_string_has_both_languages(self):
        for key, (zh, en) in i18n._STRINGS.items():
            self.assertTrue(zh and en, key)

    def test_unknown_language_falls_back(self):
        _support.use_language(self, "fr")
        self.assertEqual(i18n.language(), "zh")


class SingleInstanceTest(unittest.TestCase):
    """Only one process claims an instance; others hand over to it."""

    def test_first_claim_wins_and_others_hand_over(self):
        name = f"DesktopNote-test-single-instance-{os.getpid()}"  # Two test runs never share it.
        start = time.perf_counter()
        self.assertTrue(claim(name))
        self.assertLess(time.perf_counter() - start, 0.2, "a first launch is not delayed")
        self.assertTrue(claim(name), "claiming again in the same process keeps it")
        other = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                                "from desktopnote.instance import claim; sys.exit(0 if claim(sys.argv[2], 0.2) else 1)",
                                str(_support.ROOT), name], timeout=30)
        self.assertEqual(other.returncode, 1, "another process does not get it")


class VersionTest(unittest.TestCase):
    """The package version matches the one declared in pyproject.toml."""

    def test_version_matches_pyproject(self):
        try:
            import tomllib
        except ImportError:  # Python 3.10 has no tomllib.
            self.skipTest("tomllib needs Python 3.11+")
        import desktopnote
        declared = tomllib.loads((_support.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(desktopnote.__version__, declared)


class ReleaseBuildTest(unittest.TestCase):
    """The release build leaves out standard-library modules (UNUSED_STDLIB in scripts/build.py);
    the app imports none of them."""

    def test_the_app_imports_no_module_the_build_leaves_out(self):
        build = ast.parse((_support.ROOT / "scripts" / "build.py").read_text(encoding="utf-8"))
        left_out = next(set(ast.literal_eval(node.value)) for node in build.body if isinstance(node, ast.Assign)
                        and any(getattr(target, "id", "") == "UNUSED_STDLIB" for target in node.targets))
        for path in sorted((_support.ROOT / "desktopnote").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    with self.subTest(module=path.name, imports=name):
                        self.assertNotIn(name.split(".")[0], left_out, f"{path.name} imports {name}")


if __name__ == "__main__":
    unittest.main()
