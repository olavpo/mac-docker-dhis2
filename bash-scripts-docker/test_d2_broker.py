"""Unit tests for the pure helpers in d2-broker (no Docker required).

Loads the extension-less d2-broker script as a module via importlib.
Run: python3 -m unittest test_d2_broker -v   (from bash-scripts-docker/)
"""
import importlib.util
import importlib.machinery
import os
import unittest
from datetime import timezone
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_broker_path = os.path.join(_HERE, "d2-broker")
_loader = importlib.machinery.SourceFileLoader("d2broker", _broker_path)
broker = importlib.util.module_from_spec(
    importlib.util.spec_from_loader("d2broker", _loader))
_loader.exec_module(broker)


class ExtractTargetMajor(unittest.TestCase):
    def test_bare_major(self):
        self.assertEqual(broker.extract_target_major("42"), 42)

    def test_two_dot_major(self):
        self.assertEqual(broker.extract_target_major("2.42"), 42)

    def test_full_version(self):
        self.assertEqual(broker.extract_target_major("2.42.4"), 42)

    def test_non_numeric_returns_none(self):
        self.assertIsNone(broker.extract_target_major(""))


if __name__ == "__main__":
    unittest.main()
