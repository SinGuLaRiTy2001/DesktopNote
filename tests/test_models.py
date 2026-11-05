"""Model tests: a note's label and how older tag colours are read."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote.models import Item, Note  # noqa: E402


class NoteTest(unittest.TestCase):
    """A note's label, and the tag colours of older versions."""

    def test_label_is_the_title_then_the_first_line(self):
        self.assertEqual(Note(title="  Groceries ").label(), "Groceries")
        note = Note(items=[Item(text=""), Item(text="\n  Call   dentist \nlater")])
        self.assertEqual(note.label(), "Call dentist", "the first non-empty line, spaces collapsed")
        self.assertEqual(Note().label(), "")

    def test_legacy_tints_map_to_tags(self):
        for stored, tint in (({"tint": "yellow"}, "lemon"), ({"tint": "navy"}, "lavender"),
                             ({"tint": "#12ab9F"}, "#12ab9F"), ({"tint": "default"}, "none"), ({}, "none")):
            with self.subTest(stored=stored):
                self.assertEqual(Note.from_dict(stored).tint, tint)


if __name__ == "__main__":
    unittest.main()
