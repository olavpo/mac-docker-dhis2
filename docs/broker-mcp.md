# d2-broker-mcp — MCP server over the broker

`d2-broker-mcp` lets a local MCP client — Claude Desktop, Claude Code —
spin up disposable DHIS2 instances to test and validate things. It is a
thin stdio adapter over the [d2-broker HTTP API](./broker-api.md): the
client spawns it as a subprocess, it translates MCP tool calls into
authenticated requests to `http://localhost:9300`, and the broker does
all the actual Docker work. One dependency-free Python 3 file:
`bash-scripts-docker/d2-broker-mcp`.

## Security model

- **Agent-scoped by design.** The server authenticates with the broker's
  *agent* token (never the admin token), so the MCP client is confined to
  `agent-*` instances, the agent instance cap, and curated seeds. It
  cannot see or touch instances you created yourself, take backups, or
  deploy arbitrary WARs.
- **Local only.** MCP stdio means no port, no daemon, no network
  exposure; the broker stays loopback-bound and unchanged.
- **Fixed verb set.** Tools map 1:1 onto broker endpoints — there is no
  way to smuggle through extra `docker` or shell commands.

## Claude Desktop setup

The broker must be running (`d2-broker status`, or `d2-broker install`
for the launchd agent). Then add to `claude_desktop_config.json`
(Settings → Developer → Edit Config):

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

GUI apps do not inherit your shell environment, so `DHIS2_BASE` from
`.zshrc` is invisible to Claude Desktop — either set it in the `env`
block as above, or rely on the built-in `~/dhis2` default.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `DHIS2_BROKER_URL` | `http://localhost:9300` | Broker base URL |
| `DHIS2_BROKER_TOKEN` | *(unset)* | Bearer token; overrides the file lookup |
| `DHIS2_BASE` | `~/dhis2` | Where to find `_broker/tokens.json` (agent token) |

With nothing set, the server reads the agent token from
`~/dhis2/_broker/tokens.json` — on a machine set up with `setup.sh` it
works with zero configuration.

## Tools

| Tool | What it does |
|---|---|
| `list_instances` | List `agent-*` instances with readiness + DHIS2 version |
| `create_instance` | Create an instance (name, version, seed, memory, label) |
| `reset_instance` | Restore an instance's DB from a seed |
| `start_instance` / `stop_instance` | Containers up / down |
| `delete_instance` | Remove containers, volumes and data (irreversible) |
| `list_seeds` | Curated seed databases for create/reset |
| `get_job` | One job's state incl. `log_tail` |
| `wait_for_job` | Poll a job up to a timeout (default 60 s, max 240 s) |

Mutations return a **job** immediately (the broker is asynchronous and
serializes work globally); the model then calls `wait_for_job`
repeatedly until the job is terminal. A create on a fresh database can
take many minutes — that is Flyway, not a hang; the job's `log_tail`
shows migration progress.

## Troubleshooting

- **"broker not reachable"** — the broker isn't running: `d2-broker
  status`, then `d2-broker run` (foreground) or `d2-broker install`.
- **"no broker token"** — no `DHIS2_BROKER_TOKEN` set and
  `$DHIS2_BASE/_broker/tokens.json` unreadable. Run `d2-broker tokens`
  once on the host, and check `DHIS2_BASE` in the client's `env` block.
- **Server listed but tools error in Claude Desktop** — check the MCP
  logs (Settings → Developer): the server logs diagnostics to stderr.
- **Smoke test from a terminal:**

  ```bash
  printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_seeds","arguments":{}}}' \
    | bash-scripts-docker/d2-broker-mcp
  ```

Design spec:
[2026-07-23-broker-mcp-server-design.md](./superpowers/specs/2026-07-23-broker-mcp-server-design.md).
Tests: `bash-scripts-docker/test_d2_broker_mcp.py`
(`python3 -m unittest test_d2_broker_mcp`).
