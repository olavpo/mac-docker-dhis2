# d2-broker-mcp — MCP server for the broker (design)

Date: 2026-07-23
Status: approved

## Purpose

Give Claude Desktop (and any other local MCP client) a way to spin up
disposable DHIS2 instances to test and validate things — the same use
case the `dhis2-instances` skill serves for sandboxed agents. This is
*not* a general instance-management surface: the server is agent-scoped
by design and never handles the admin token.

## Decisions (from brainstorming)

- **Client**: Claude Desktop / Claude Code on this Mac only. No remote
  access; the broker stays loopback-bound and unchanged.
- **Transport**: MCP stdio (newline-delimited JSON-RPC 2.0 on
  stdin/stdout). The client spawns the process; no daemon, no port.
- **Scope**: the broker's **agent** token. Confined to `agent-*`
  instances, instance cap, curated seeds only. The server never reads
  the admin token from `tokens.json`.
- **Implementation**: pure-stdlib Python 3, hand-rolled MCP protocol
  subset — consistent with the repo's zero-dependency ethos. No
  FastMCP, no uv, no install step.

## Shape

One executable, extension-less Python file:
`bash-scripts-docker/d2-broker-mcp`.

Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "dhis2-instances": {
      "command": "python3",
      "args": ["/Users/olavpo/Repos/dhis2-docker-tools/bash-scripts-docker/d2-broker-mcp"],
      "env": { "DHIS2_BASE": "/Users/olavpo/dhis2" }
    }
  }
}
```

## Protocol subset

Tools-only MCP server. Methods handled:

| Method | Behaviour |
|---|---|
| `initialize` | Return `protocolVersion` (echo the client's if we know it, else our latest supported), `capabilities: {"tools": {}}`, `serverInfo`. |
| `notifications/initialized` | Notification; no response. |
| `ping` | Empty result `{}`. |
| `tools/list` | Static, hand-written tool list with JSON Schema `inputSchema`s. |
| `tools/call` | Dispatch to the matching tool function. |
| anything else | JSON-RPC error `-32601` (method not found); notifications other than `initialized` are ignored. |

Framing: one JSON-RPC message per line (MCP stdio framing). Anything the
server wants to log goes to **stderr**; stdout carries protocol messages
only. Malformed JSON on a line → JSON-RPC parse error `-32700` (with
`id: null`). EOF on stdin → clean exit.

Tool results are `content: [{"type": "text", "text": <pretty-printed
JSON>}]`. Failures return `isError: true` with the broker's error
message verbatim (they are already user-facing).

## Configuration & auth

Resolved lazily on first tool call, cached after:

1. `DHIS2_BROKER_URL` env var, default `http://localhost:9300`.
2. `DHIS2_BROKER_TOKEN` env var if set; otherwise read the **agent**
   token from `$DHIS2_BASE/_broker/tokens.json`, where `DHIS2_BASE`
   defaults to `~/dhis2` (Claude Desktop is GUI-spawned and does not
   inherit shell env, so the fallback matters).
3. If no token can be resolved, tool calls return a clear `isError`
   explaining what to set — the server still starts and handshakes, so
   the failure is visible in the conversation rather than as a dead
   server.

## Tool surface

Nine tools, thin wrappers over broker endpoints via `urllib.request`
(connect/read timeouts; the broker answers fast — long work is a job):

| Tool | Endpoint | Notes |
|---|---|---|
| `list_instances` | `GET /instances?full=1` | Readiness + version included; agent scope already filters to `agent-*`. |
| `create_instance` | `POST /instances` | Args: `name` (required, must start with `agent-`), `version`, `seed`, `memory`, `label`. No `war_url`/`war_file`/`analytics`/port args. |
| `reset_instance` | `POST /instances/<name>/reset` | Arg: `seed` (required). |
| `start_instance` | `POST /instances/<name>/start` | |
| `stop_instance` | `POST /instances/<name>/stop` | |
| `delete_instance` | `DELETE /instances/<name>` | Description warns: irreversible. |
| `list_seeds` | `GET /seeds` | Curated seeds only under agent scope. |
| `get_job` | `GET /jobs/<id>` | Includes `log_tail`. |
| `wait_for_job` | polls `GET /jobs/<id>` | Args: `job_id`, `timeout_seconds` (default 60, max 240). Poll every 2 s; return the job (with `log_tail`) when terminal or when the timeout lapses (still `running` → caller calls again). |

Mutation tools return the broker's `202` response (job id + poll paths)
immediately. Tool descriptions carry the ergonomics: mutations return a
job to wait on with `wait_for_job`; creates on a fresh DB can take many
minutes; `readiness: ready` means metadata-usable, not analytics-ready;
instance names must start with `agent-`.

No `backup` or `upgrade` tools (agent scope forbids backup; upgrade is
out of scope for "spin up and test" — YAGNI).

## Errors

- Broker `4xx` JSON error bodies → tool `isError` with the message
  verbatim.
- Connection refused / timeout to the broker → `isError` with a hint:
  "broker not reachable at <url> — is it running? (d2-broker status)".
- Unknown tool name in `tools/call` → JSON-RPC error `-32602`.

## Testing

`bash-scripts-docker/test_d2_broker_mcp.py`, same conventions as
`test_d2_broker.py` (unittest, load the extension-less script via
`SourceFileLoader`, run with `python3 -m unittest`). No Docker, no real
broker:

- Protocol: initialize handshake shape, `tools/list` returns all nine
  tools with valid schemas, unknown method → `-32601`, parse error,
  notifications produce no output.
- Dispatch: `tools/call` against a stubbed broker HTTP layer (a local
  `ThreadingHTTPServer` stand-in, as `test_d2_broker.py` already does
  for readiness probes) — create round-trip, error body mapped to
  `isError`, connection-refused hint.
- Config: token fallback order (env var beats tokens.json), missing
  token → helpful `isError`.
- `wait_for_job`: returns immediately on terminal job; returns
  still-running job at timeout.

## Documentation

`docs/broker-mcp.md`: what it is, one-paragraph security model (agent
scope, loopback, no admin token), the Claude Desktop config snippet,
troubleshooting (env not inherited from shell, broker not running).
Cross-link from `docs/broker.md` / `docs/broker-api.md` client lists.
