# Contributing to Woffu Helper

Bug reports, clearer setup instructions and focused pull requests are welcome. Describe the expected behavior, the actual behavior, your Python version and the command or MCP tool involved. Use synthetic records and redact tokens, company hostnames, employee IDs and personal time records.

## Local development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python -m unittest discover -v
```

MCP development needs Python 3.10+. For base CLI work on Python 3.9+, install `-e .` instead; the optional protocol tests skip when the SDK is absent. The remaining tests cover configuration, credentials, lunch timing, recovery and MCP repair logic. No test needs a real Woffu account.

CI runs base installations on Python 3.9/3.13 and MCP installations on 3.10/3.14, including the stdio handshake and tool calls. Packaging checks build both a wheel and source distribution.

## Changes to review carefully

- Keep the original source-script commands, console entry points and no-config schedule defaults compatible.
- Keep the MCP SDK optional. Do not import it from the scheduler/client modules.
- Preserve recorded sign identity, local `HH:mm:ss` timestamps, metadata, motives and comments during a close.
- Check actual slots rather than trusting the summary's last clock-out.
- Respect calendar, absence, lock, approval and pause restrictions in the relevant repair paths.
- Keep stdout reserved for MCP protocol messages. Never log credentials or raw HTTP authorization headers.
- Exercise meaningful failure cases when changing writes: stale plans, duplicate attempts, timeouts and protected days.

Update the relevant guide and changelog when behavior changes. Do not verify changes by mutating a real employee's diary. Report security issues through [the security policy](SECURITY.md).
