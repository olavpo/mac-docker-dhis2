"""Unit tests for the pure helpers in d2-broker (no Docker required).

Loads the extension-less d2-broker script as a module via importlib.
Run: python3 -m unittest test_d2_broker -v   (from bash-scripts-docker/)
"""
import importlib.util
import importlib.machinery
import os
import tempfile
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


class TransitionError(unittest.TestCase):
    def test_same_major_ok(self):
        self.assertIsNone(broker.transition_error(42, 42))

    def test_one_up_ok(self):
        self.assertIsNone(broker.transition_error(41, 42))

    def test_downgrade_rejected(self):
        msg = broker.transition_error(42, 41)
        self.assertIsNotNone(msg)
        self.assertIn("downgrade", msg)

    def test_major_skip_rejected(self):
        msg = broker.transition_error(42, 44)
        self.assertIsNotNone(msg)
        self.assertIn("skipping", msg)

    def test_unknown_current_skips_guard(self):
        self.assertIsNone(broker.transition_error(None, 42))


class ParseTomcatMajor(unittest.TestCase):
    def test_tomcat_10(self):
        self.assertEqual(broker.parse_tomcat_major("tomcat:10-jdk17"), 10)

    def test_tomcat_9(self):
        self.assertEqual(broker.parse_tomcat_major("tomcat:9.0-jdk17"), 9)

    def test_empty(self):
        self.assertIsNone(broker.parse_tomcat_major(""))

    def test_no_match(self):
        self.assertIsNone(broker.parse_tomcat_major("postgis:16-3.5"))


class DumpElement(unittest.TestCase):
    def test_backups_path_prefixed_and_relative(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "agent-x" / "agent-x_20260614-091401_v42.sql.gz"
            f.parent.mkdir(parents=True)
            f.write_bytes(b"hello")
            elem = broker.dump_element(f, "backups", root)
            self.assertEqual(
                elem["path"],
                "backups/agent-x/agent-x_20260614-091401_v42.sql.gz")
            self.assertEqual(elem["source"], "backups")
            self.assertEqual(elem["size_bytes"], 5)
            self.assertTrue(elem["modified"].endswith("+00:00"))

    def test_seeds_path_not_prefixed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "demo.sql.gz"
            f.write_bytes(b"x")
            elem = broker.dump_element(f, "seeds", root)
            self.assertEqual(elem["path"], "demo.sql.gz")
            self.assertEqual(elem["source"], "seeds")


class MemToMb(unittest.TestCase):
    def test_megabytes(self):
        self.assertEqual(broker.mem_to_mb("512m"), 512)

    def test_gigabytes(self):
        self.assertEqual(broker.mem_to_mb("2g"), 2048)

    def test_four_g(self):
        self.assertEqual(broker.mem_to_mb("4g"), 4096)

    def test_uppercase_unit(self):
        self.assertEqual(broker.mem_to_mb("1G"), 1024)

    def test_unparseable_returns_none(self):
        for bad in ("", "4", "4gb", "abc", "4k", "2.5g"):
            self.assertIsNone(broker.mem_to_mb(bad))


class MemoryRe(unittest.TestCase):
    def test_matches(self):
        for good in ("512m", "2g", "4096m", "1G", "8M"):
            self.assertIsNotNone(broker.MEMORY_RE.match(good))

    def test_rejects(self):
        for bad in ("", "4", "4gb", "4k", "2.5g", " 2g"):
            self.assertIsNone(broker.MEMORY_RE.match(bad))


class ValidPort(unittest.TestCase):
    def test_valid(self):
        for p in (1024, 65535, 8080, "9010"):
            self.assertTrue(broker.valid_port(p), p)

    def test_invalid(self):
        for p in (1023, 70000, 0, "abc", None, "80.5", ""):
            self.assertFalse(broker.valid_port(p), p)


if __name__ == "__main__":
    unittest.main()
