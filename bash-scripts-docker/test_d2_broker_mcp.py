"""Unit tests for d2-broker-mcp (no Docker, no real broker required).

Loads the extension-less d2-broker-mcp script as a module via importlib.
Run: python3 -m unittest test_d2_broker_mcp -v   (from bash-scripts-docker/)
"""
import importlib.util
import importlib.machinery
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_mcp_path = os.path.join(_HERE, "d2-broker-mcp")
_loader = importlib.machinery.SourceFileLoader("d2brokermcp", _mcp_path)
mcp = importlib.util.module_from_spec(
    importlib.util.spec_from_loader("d2brokermcp", _loader))
_loader.exec_module(mcp)


def req(method, params=None, id_=1):
    m = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        m["params"] = params
    return m


def notif(method, params=None):
    m = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        m["params"] = params
    return m


def call(name, arguments=None):
    return req("tools/call",
               {"name": name, "arguments": arguments or {}})


class EnvMixin:
    """Set env vars for a test, restoring afterwards."""

    def set_env(self, **kv):
        for key, value in kv.items():
            old = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
            self.addCleanup(
                lambda k=key, o=old: os.environ.update({k: o}) if o is not None
                else os.environ.pop(k, None))


class StubBroker:
    """Local HTTP server standing in for d2-broker.

    routes: {("POST", "/instances"): (202, {...}), ...} — path includes
    the query string. Records every request as (method, path, headers,
    body-dict-or-None).
    """

    def __init__(self, testcase, routes):
        self.routes = routes
        self.requests = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                stub.requests.append(
                    (self.command, self.path, dict(self.headers), body))
                status, payload = stub.routes.get(
                    (self.command, self.path),
                    (404, {"error": "no such route in stub"}))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _handle

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        testcase.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class Handshake(unittest.TestCase):
    def test_initialize_shape(self):
        resp = mcp.handle_message(req("initialize", {
            "protocolVersion": mcp.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}))
        self.assertEqual(resp["id"], 1)
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], mcp.PROTOCOL_VERSION)
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "d2-broker-mcp")

    def test_initialize_unknown_client_version_answers_ours(self):
        resp = mcp.handle_message(req("initialize", {
            "protocolVersion": "1999-01-01", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}))
        self.assertEqual(resp["result"]["protocolVersion"],
                         mcp.PROTOCOL_VERSION)

    def test_initialized_notification_no_response(self):
        self.assertIsNone(mcp.handle_message(notif("notifications/initialized")))

    def test_other_notifications_ignored(self):
        self.assertIsNone(mcp.handle_message(notif("notifications/cancelled")))

    def test_ping(self):
        self.assertEqual(mcp.handle_message(req("ping"))["result"], {})

    def test_unknown_method_is_32601(self):
        resp = mcp.handle_message(req("resources/list"))
        self.assertEqual(resp["error"]["code"], -32601)


class ToolsList(unittest.TestCase):
    def test_all_ten_tools(self):
        resp = mcp.handle_message(req("tools/list"))
        tools = resp["result"]["tools"]
        self.assertEqual(
            {t["name"] for t in tools},
            {"list_instances", "create_instance", "reset_instance",
             "start_instance", "stop_instance", "delete_instance",
             "run_analytics", "list_seeds", "get_job", "wait_for_job"})

    def test_every_tool_has_description_and_object_schema(self):
        for t in mcp.handle_message(req("tools/list"))["result"]["tools"]:
            self.assertTrue(t["description"], t["name"])
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])

    def test_unknown_tool_is_32602(self):
        resp = mcp.handle_message(call("upgrade_instance"))
        self.assertEqual(resp["error"]["code"], -32602)


def tool_result(resp):
    """Unpack a tools/call response into (text, is_error)."""
    result = resp["result"]
    return result["content"][0]["text"], result.get("isError", False)


class ToolCalls(EnvMixin, unittest.TestCase):
    def setUp(self):
        self.job = {"id": "j-1a2b3c4d", "op": "create",
                    "instance": "agent-x", "status": "queued"}

    def _broker(self, routes):
        stub = StubBroker(self, routes)
        self.set_env(DHIS2_BROKER_URL=stub.url, DHIS2_BROKER_TOKEN="testtok")
        return stub

    def test_create_round_trip(self):
        stub = self._broker(
            {("POST", "/instances"): (202, {"job": self.job})})
        text, is_error = tool_result(mcp.handle_message(call(
            "create_instance", {"name": "agent-x", "version": "2.42"})))
        self.assertFalse(is_error)
        self.assertIn("j-1a2b3c4d", text)
        method, path, headers, body = stub.requests[0]
        self.assertEqual((method, path), ("POST", "/instances"))
        self.assertEqual(headers.get("Authorization"), "Bearer testtok")
        self.assertEqual(body, {"name": "agent-x", "version": "2.42"})

    def test_optional_create_args_omitted_from_body(self):
        stub = self._broker(
            {("POST", "/instances"): (202, {"job": self.job})})
        mcp.handle_message(call("create_instance", {"name": "agent-x"}))
        self.assertEqual(stub.requests[0][3], {"name": "agent-x"})

    def test_list_instances_uses_full(self):
        stub = self._broker(
            {("GET", "/instances?full=1"): (200, {"instances": []})})
        text, is_error = tool_result(
            mcp.handle_message(call("list_instances")))
        self.assertFalse(is_error)
        self.assertEqual(stub.requests[0][0], "GET")

    def test_delete_uses_delete_method(self):
        stub = self._broker(
            {("DELETE", "/instances/agent-x"): (202, {"job": self.job})})
        text, is_error = tool_result(mcp.handle_message(call(
            "delete_instance", {"name": "agent-x"})))
        self.assertFalse(is_error)
        self.assertEqual(stub.requests[0][0], "DELETE")

    def test_broker_error_verbatim(self):
        self._broker({("POST", "/instances"):
                      (409, {"error": "instance already exists"})})
        text, is_error = tool_result(mcp.handle_message(call(
            "create_instance", {"name": "agent-x"})))
        self.assertTrue(is_error)
        self.assertIn("instance already exists", text)

    def test_connection_refused_hint(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here now
        self.set_env(DHIS2_BROKER_URL=f"http://127.0.0.1:{port}",
                     DHIS2_BROKER_TOKEN="testtok")
        text, is_error = tool_result(
            mcp.handle_message(call("list_seeds")))
        self.assertTrue(is_error)
        self.assertIn("not reachable", text)
        self.assertIn(str(port), text)


class TokenResolution(EnvMixin, unittest.TestCase):
    def _base_with_tokens(self, content):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        if content is not None:
            broker_dir = Path(d.name) / "_broker"
            broker_dir.mkdir()
            (broker_dir / "tokens.json").write_text(json.dumps(content))
        return d.name

    def test_env_var_wins(self):
        base = self._base_with_tokens(
            {"admin": {"token": "a"}, "agent": {"token": "g"}})
        self.set_env(DHIS2_BROKER_TOKEN="envtok", DHIS2_BASE=base)
        self.assertEqual(mcp.resolve_token(), "envtok")

    def test_agent_token_from_file_never_admin(self):
        base = self._base_with_tokens(
            {"admin": {"token": "a"}, "agent": {"token": "g"}})
        self.set_env(DHIS2_BROKER_TOKEN=None, DHIS2_BASE=base)
        self.assertEqual(mcp.resolve_token(), "g")

    def test_missing_token_yields_helpful_error(self):
        base = self._base_with_tokens(None)
        self.set_env(DHIS2_BROKER_TOKEN=None, DHIS2_BASE=base)
        text, is_error = tool_result(
            mcp.handle_message(call("list_seeds")))
        self.assertTrue(is_error)
        self.assertIn("DHIS2_BROKER_TOKEN", text)


class WaitForJob(EnvMixin, unittest.TestCase):
    def _broker(self, job):
        stub = StubBroker(
            self, {("GET", "/jobs/j-1"): (200, job)})
        self.set_env(DHIS2_BROKER_URL=stub.url, DHIS2_BROKER_TOKEN="t")
        return stub

    def test_terminal_job_returns_immediately(self):
        stub = self._broker({"id": "j-1", "status": "succeeded",
                             "result": {"name": "agent-x"}})
        text, is_error = tool_result(mcp.handle_message(call(
            "wait_for_job", {"job_id": "j-1"})))
        self.assertFalse(is_error)
        self.assertIn("succeeded", text)
        self.assertEqual(len(stub.requests), 1)

    def test_timeout_returns_running_job(self):
        self._broker({"id": "j-1", "status": "running",
                      "log_tail": "migrating..."})
        text, is_error = tool_result(mcp.handle_message(call(
            "wait_for_job", {"job_id": "j-1", "timeout_seconds": 0})))
        self.assertFalse(is_error)
        self.assertIn("running", text)


class ParseErrors(unittest.TestCase):
    def test_malformed_json_line(self):
        resp = mcp.handle_line("{not json")
        self.assertEqual(resp["error"]["code"], -32700)
        self.assertIsNone(resp["id"])

    def test_blank_line_ignored(self):
        self.assertIsNone(mcp.handle_line("   "))


if __name__ == "__main__":
    unittest.main()
