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

    def test_dhis2_version_from_filename_token(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "dhis2-db-sierra-leone_v42.sql.gz"
            f.write_bytes(b"x")
            self.assertEqual(
                broker.dump_element(f, "seeds", root)["dhis2_version"], 42)

    def test_dhis2_version_none_without_token(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "demo.sql.gz"
            f.write_bytes(b"x")
            self.assertIsNone(
                broker.dump_element(f, "seeds", root)["dhis2_version"])


class InstanceMeta(unittest.TestCase):
    def _with_base(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        old = os.environ.get("DHIS2_BASE")
        os.environ["DHIS2_BASE"] = d.name
        self.addCleanup(
            lambda: os.environ.update({"DHIS2_BASE": old}) if old
            else os.environ.pop("DHIS2_BASE", None))
        return d.name

    def test_roundtrip_with_label(self):
        self._with_base()
        broker.write_instance_meta("agent-x", "review of tool-y")
        meta = broker.read_instance_meta("agent-x")
        self.assertEqual(meta["label"], "review of tool-y")
        self.assertTrue(meta["created_at"].endswith("+00:00"))

    def test_no_label_omitted(self):
        self._with_base()
        broker.write_instance_meta("agent-x")
        meta = broker.read_instance_meta("agent-x")
        self.assertNotIn("label", meta)
        self.assertIn("created_at", meta)

    def test_missing_file_returns_empty(self):
        self._with_base()
        self.assertEqual(broker.read_instance_meta("nope"), {})

    def test_recreate_overwrites(self):
        self._with_base()
        broker.write_instance_meta("agent-x", "old")
        broker.write_instance_meta("agent-x", "new")
        self.assertEqual(broker.read_instance_meta("agent-x")["label"], "new")


class InstanceReadiness(unittest.TestCase):
    """Probes a throwaway local HTTP server standing in for the instance."""

    def _serve(self, status, body=b"{}"):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def _inst(self, port, status="running"):
        return {"status": status, "http_port": port}

    def test_not_running_is_none(self):
        self.assertIsNone(broker.instance_readiness(self._inst(1, "stopped")))

    def test_no_port_is_none(self):
        self.assertIsNone(broker.instance_readiness(self._inst(None)))

    def test_200_with_version_is_ready(self):
        port = self._serve(200, b'{"version": "2.42.5"}')
        self.assertEqual(broker.instance_readiness(self._inst(port)), "ready")

    def test_200_without_version_is_migrating(self):
        port = self._serve(200, b'{"contextPath": "x"}')
        self.assertEqual(
            broker.instance_readiness(self._inst(port)), "migrating")

    def test_401_is_ready(self):
        port = self._serve(401)
        self.assertEqual(broker.instance_readiness(self._inst(port)), "ready")

    def test_404_is_deploying(self):
        port = self._serve(404)
        self.assertEqual(
            broker.instance_readiness(self._inst(port)), "deploying")

    def test_503_is_migrating(self):
        port = self._serve(503)
        self.assertEqual(
            broker.instance_readiness(self._inst(port)), "migrating")

    def test_connection_refused_is_deploying(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here now
        self.assertEqual(
            broker.instance_readiness(self._inst(port)), "deploying")


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
        for p in (1023, 70000, 0, "abc", None, "80.5", "", 9010.5):
            self.assertFalse(broker.valid_port(p), p)


class ParseAnalyticsProfiles(unittest.TestCase):
    def test_doris_profile(self):
        self.assertEqual(
            broker.parse_analytics_profiles("COMPOSE_PROFILES=analytics-doris\n"),
            "doris")

    def test_doris_among_multiple_profiles(self):
        self.assertEqual(
            broker.parse_analytics_profiles("COMPOSE_PROFILES=foo, analytics-doris ,bar"),
            "doris")

    def test_no_profile_line(self):
        self.assertIsNone(broker.parse_analytics_profiles("SOMETHING=else\n"))

    def test_other_profiles_only(self):
        self.assertIsNone(
            broker.parse_analytics_profiles("COMPOSE_PROFILES=debug"))

    def test_empty_and_none(self):
        self.assertIsNone(broker.parse_analytics_profiles(""))
        self.assertIsNone(broker.parse_analytics_profiles(None))


class AnalyticsRequestError(unittest.TestCase):
    def test_valid_doris_42(self):
        self.assertIsNone(broker.analytics_request_error("doris", "42"))
        self.assertIsNone(broker.analytics_request_error("doris", "2.42.4"))
        self.assertIsNone(broker.analytics_request_error("doris", "2.43"))

    def test_unknown_backend(self):
        self.assertIn("doris", broker.analytics_request_error("clickhouse", "42"))

    def test_missing_version(self):
        self.assertIn("version", broker.analytics_request_error("doris", None))
        self.assertIn("version", broker.analytics_request_error("doris", ""))

    def test_major_below_42(self):
        self.assertIn("42", broker.analytics_request_error("doris", "2.41"))
        self.assertIn("42", broker.analytics_request_error("doris", "40"))

    def test_non_numeric_major(self):
        self.assertIsNotNone(broker.analytics_request_error("doris", "abc"))


class RequiredTomcatMajor(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(broker.required_tomcat_major(40), 9)
        self.assertEqual(broker.required_tomcat_major(41), 9)
        self.assertEqual(broker.required_tomcat_major(42), 10)
        self.assertEqual(broker.required_tomcat_major(43), 10)

    def test_none(self):
        self.assertIsNone(broker.required_tomcat_major(None))


class JobsToPrune(unittest.TestCase):
    @staticmethod
    def _job(i, status="succeeded"):
        return {"id": f"j-{i:04d}", "status": status,
                "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00"}

    def test_under_limit_keeps_all(self):
        jobs = [self._job(i) for i in range(5)]
        self.assertEqual(broker.jobs_to_prune(jobs, keep=10), [])

    def test_oldest_beyond_keep_pruned(self):
        jobs = [self._job(i) for i in range(10)]
        pruned = broker.jobs_to_prune(jobs, keep=7)
        self.assertEqual(sorted(pruned), ["j-0000", "j-0001", "j-0002"])

    def test_active_jobs_never_pruned(self):
        jobs = [self._job(0, "running"), self._job(1, "queued")]
        jobs += [self._job(i) for i in range(2, 10)]
        pruned = broker.jobs_to_prune(jobs, keep=3)
        self.assertNotIn("j-0000", pruned)
        self.assertNotIn("j-0001", pruned)
        # Oldest terminal ones go, newest 3 stay.
        self.assertEqual(sorted(pruned),
                         [f"j-{i:04d}" for i in range(2, 7)])


class ParseHostPort(unittest.TestCase):
    def test_dual_stack(self):
        self.assertEqual(broker.parse_host_port("0.0.0.0:9010\n[::]:9010\n"), 9010)

    def test_ipv6_only(self):
        self.assertEqual(broker.parse_host_port("[::]:9012\n"), 9012)

    def test_specific_ipv4(self):
        self.assertEqual(broker.parse_host_port("127.0.0.1:8080\n"), 8080)

    def test_empty(self):
        self.assertIsNone(broker.parse_host_port(""))

    def test_garbage(self):
        self.assertIsNone(broker.parse_host_port("Error: no such container\n"))


class SeedMajor(unittest.TestCase):
    def test_curated_seed(self):
        self.assertEqual(broker.seed_major("dhis2-db-sierra-leone_v42.sql.gz"), 42)

    def test_uppercase_token(self):
        self.assertEqual(broker.seed_major("dhis2-db-sierra-leone_V40.sql.gz"), 40)

    def test_backup_with_label(self):
        self.assertEqual(broker.seed_major("acdc_2026-03-03-1200_v41_pre.sql.gz"), 41)

    def test_last_token_wins(self):
        # An instance named like inst_v41 must not shadow the real version.
        self.assertEqual(broker.seed_major("inst_v41_2026-03-03_v42.sql.gz"), 42)

    def test_full_path_uses_basename(self):
        self.assertEqual(broker.seed_major("/x/_seeds/demo_v43.sql"), 43)

    def test_no_token(self):
        self.assertIsNone(broker.seed_major("plain-dump.sql.gz"))


class SeedResetError(unittest.TestCase):
    def test_newer_seed_refused(self):
        msg = broker.seed_reset_error(42, 41)
        self.assertIsNotNone(msg)
        self.assertIn("42", msg)

    def test_older_seed_ok(self):
        self.assertIsNone(broker.seed_reset_error(41, 42))

    def test_same_major_ok(self):
        self.assertIsNone(broker.seed_reset_error(42, 42))

    def test_unknown_seed_skips_guard(self):
        self.assertIsNone(broker.seed_reset_error(None, 42))

    def test_unknown_instance_skips_guard(self):
        self.assertIsNone(broker.seed_reset_error(42, None))


class SubmitExclusive(unittest.TestCase):
    """submit() itself must reject an instance with an active job (under one
    lock hold) — the handler's early check_no_active_job is not atomic with
    the enqueue."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_base = os.environ.get("DHIS2_BASE")
        os.environ["DHIS2_BASE"] = self._tmp.name
        # Keep the worker thread inert so submitted jobs stay queued and
        # nothing writes to the temp dir behind the test's back.
        self._real_worker = broker.JobManager._worker
        broker.JobManager._worker = lambda self: None
        self.mgr = broker.JobManager()

    def tearDown(self):
        broker.JobManager._worker = self._real_worker
        if self._old_base is None:
            os.environ.pop("DHIS2_BASE", None)
        else:
            os.environ["DHIS2_BASE"] = self._old_base
        self._tmp.cleanup()

    def _fake_active(self, instance, status):
        job = {"id": "j-fake", "op": "create", "instance": instance,
               "status": status, "created_at": broker.now_iso()}
        self.mgr.jobs[job["id"]] = job

    def test_active_job_rejected(self):
        self._fake_active("foo", "running")
        with self.assertRaises(broker.ApiError) as ctx:
            self.mgr.submit("stop", "foo", [])
        self.assertEqual(ctx.exception.status, 409)

    def test_queued_job_rejected(self):
        self._fake_active("foo", "queued")
        with self.assertRaises(broker.ApiError):
            self.mgr.submit("stop", "foo", [])

    def test_other_instance_ok(self):
        self._fake_active("foo", "running")
        job = self.mgr.submit("stop", "bar", [])
        self.assertEqual(job["instance"], "bar")

    def test_terminal_job_ok(self):
        self._fake_active("foo", "failed")
        job = self.mgr.submit("stop", "foo", [])
        self.assertEqual(job["instance"], "foo")


if __name__ == "__main__":
    unittest.main()
