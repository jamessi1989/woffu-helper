# Woffu MCP server: setup and tool reference

[Woffu Helper](../README.md) includes a local Model Context Protocol server, `woffu-mcp`, for AI assistants that can launch stdio tools. It reads Woffu records, finds missing days and clock-outs, previews repairs, and optionally submits them.

## Where it runs

Each person installs it on the computer running their compatible AI client. The client starts `woffu-mcp` as a child process and communicates through stdin/stdout. That process calls Woffu using the person's own local credentials. There is no listening port or public server to operate.

The MCP process runs while the AI client needs it. For unattended signing, use the independent `woffu-autosign` scheduler. A remote computer can run the connector only if your client supports executing tools there and the configuration is present there.

## Install and configure

Use **Python 3.10+**. From the cloned repository:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[mcp]'
```

Follow the [credential setup](USAGE.md#credentials). For a new installation, copy [config.example.json](../config.example.json) to `~/.config/woffu/config.json` and adjust the schedule. The example has a 30-minute lunch, zero extra time/variance, and automatic recovery off. Without a configuration file, MCP uses the same [legacy defaults](USAGE.md#configure-automatic-signing) as autosign.

```bash
.venv/bin/woffu-autosign --config ~/.config/woffu/config.json --show-config
.venv/bin/woffu-mcp --help
```

`woffu-mcp` accepts `--config`, `--env-file`, and `--allow-writes`. `WOFFU_CONFIG`, `WOFFU_ENV_FILE`, `WOFFU_DOMAIN` and `WOFFU_TOKEN` follow the CLI's precedence rules. Keep tokens in the local credential file.

## Connect an AI client

### Codex CLI

```bash
codex mcp add woffu -- "$HOME/woffu-helper/.venv/bin/woffu-mcp" \
  --config "$HOME/.config/woffu/config.json" --allow-writes
codex mcp list
```

Omit `--allow-writes` for inspection and previews only. The flag makes the apply tool available; it does not submit anything at startup. Restart the connection after changing it.

Alternatively, merge this table into `~/.codex/config.toml`, replacing `/absolute/path`:

```toml
[mcp_servers.woffu]
command = "/absolute/path/woffu-helper/.venv/bin/woffu-mcp"
args = ["--config", "/absolute/path/woffu-config.json", "--allow-writes"]
```

These formats follow the [OpenAI MCP guide](https://learn.chatgpt.com/docs/extend/mcp).

### ChatGPT desktop

In desktop versions that expose **Settings → MCP servers → Add server**, choose **STDIO**, set the command to the absolute `woffu-mcp` executable path, and add the config path and optional `--allow-writes` arguments. Save and restart as directed. The [official documentation](https://learn.chatgpt.com/docs/extend/mcp) describes supported surfaces and shared Codex-host configuration.

ChatGPT web uses remote tools through supported integrations/plugins. This release provides no hosted plugin or HTTP endpoint, and ChatGPT web does not load local Codex configuration. A GitHub URL is documentation, not an MCP connection URL.

### Clients using `mcpServers` JSON

For clients that accept this format, merge this entry into their MCP settings. Exact settings locations depend on the client.

```json
{
  "mcpServers": {
    "woffu": {
      "command": "/absolute/path/woffu-helper/.venv/bin/woffu-mcp",
      "args": [
        "--config", "/absolute/path/woffu-config.json",
        "--env-file", "/absolute/path/woffu-credentials",
        "--allow-writes"
      ]
    }
  }
}
```

On Windows, use the absolute `.venv\Scripts\woffu-mcp.exe` path and escape backslashes in JSON. Paths belong to the computer launching the process. GUI apps may not expand `~` or shell variables in JSON.

## Example conversation

1. “Show my Woffu schedule and inspect 26–30 October 2026 for missing entries.”
2. “I worked 09:00–13:00 and 13:30–17:30 on Friday. Preview filling that day.”
3. “Apply that preview.”

The assistant receives existing and proposed times plus a plan ID. Applying that ID submits the stored times exactly. An empty diary does not establish that you worked; provide actual hours or authorize the configured schedule.

## Tool reference

| Tool | Arguments | Result / behavior |
|---|---|---|
| `woffu_get_config` | None | Schedule, local time/timezone, version and write capability. Does not load credentials or call Woffu. |
| `woffu_today` | None | Today's clock times, breaks, clocked-in/out state and worked seconds including an open slot. |
| `woffu_inspect_days` | `from_date`, `to_date` | Inclusive local `YYYY-MM-DD` range, 1–31 days, no future dates. Reports empty/open/complete/review states, repair eligibility, protection reasons and pending approval. |
| `woffu_preview_fill` | `day`, optional `slots` | One empty past workday. Omit slots for its configured weekday schedule, or supply exact periods such as `09:00-13:00,13:30-17:30`. |
| `woffu_preview_close` | `day`, optional `hours` | One final open slot. Omit hours for the configured weekday target plus extra time, or specify a total worked-hour target. Preserves existing signs and breaks. |
| `woffu_apply_plan` | `plan_id` | Available only with `--allow-writes`. Rechecks and submits the exact stored plan. |

Successful results include structured JSON and text. No tool accepts a Woffu token, hostname, arbitrary API path, shell command or user ID. There is no live clock toggle or approval tool.

### Preview and apply behavior

- Plans are held in memory, expire after **15 minutes**, and are single-use. Restarting clears them. At most 100 previews are retained; the oldest is evicted when full.
- Before submission, the tool rereads the diary and actual slots. A changed account, diary ID, summary times, comment or slot payload invalidates the preview. It also rechecks calendar restrictions, approval/lock state, pending edits and pause files.
- The write tool is absent in the default read-only mode. Client approval settings still apply; assistants should act within the user's authorization.
- A failed or uncertain submission consumes the plan. Inspect the day before creating another: a timed-out request may already have succeeded.
- Success means **submitted to Woffu**, not approved by the company. Inspection reports Woffu's `isPending` as `pending_approval`.
- Rechecking reduces stale edits, but Woffu's replacement endpoint does not provide an atomic compare-and-swap. Avoid simultaneous manual edits or repair processes for the same date.

### Schedule details

Default fill previews reuse autosign's date-specific schedule, weekday overrides, extra minutes and variance. Explicit `slots` have no random variation and must meet the configured minimum break (30 minutes by default). Set schedule variances to zero for exact timing in all default previews.

Closing adds only the missing final clock-out. Like the CLI, it gives the final open slot at least 30 minutes even if the target is already met. Existing short breaks are preserved. Missing lunch or afternoon signs in a fully closed day cannot be inferred or repaired.

MCP honors configured weekdays, Woffu's calendar and pause files. It skips holidays, approved or pending absences, accepted/locked days and pending edits. Today/future dates cannot be repaired, and dates absent from the API response are not treated as empty workdays. Times use the server computer's local timezone; overnight shifts are unsupported.

## Troubleshooting

| Symptom | Check |
|---|---|
| “MCP requires Python 3.10+…” | Create a Python 3.10+ venv and install `.[mcp]` into it. |
| Command not found | Use the absolute venv executable path on the machine launching it. |
| Running the command waits silently | It is waiting for an MCP client on stdin. Use `--help` for help. |
| Expired token / HTTP 401 | Refresh the token from Woffu and update the credential file. |
| No apply tool | Add `--allow-writes` to launch arguments and reconnect. |
| Unknown/expired/used plan | Inspect the day and create a fresh preview in the same connection. |
| Day blocked | Check the protection reason, workweek and pause files. |
| Shifted times | Check the computer's timezone and the work schedule. |

Tool results expose time records to the connected AI client. Credentials stay in the local process; the client's data handling policies apply to tool inputs and outputs. See [security reporting](../SECURITY.md).
