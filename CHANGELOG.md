# Changelog

## 0.3.0 — 2026-11-02

- Add the optional `woffu-mcp` local stdio server using the official MCP Python SDK. Python 3.10+ is required for the `mcp` extra; the base CLI keeps Python 3.9+ support.
- Add tools for configuration, today's entries, date-range inspection, fill/clock-out previews and applying a stored preview. Write tools require `--allow-writes`.
- Recheck account, records and edit eligibility before a write; expire and consume plans to prevent stale or duplicate submissions. Preserve signs and comments, and distinguish submission from company approval.
- Add MCP protocol/stdio and repair tests, with expanded CI and package build checks.
- Refresh the README, add a Spanish guide, detailed MCP documentation, assistant integration notes, documentation index, contribution and security guidance.

Existing scheduler behavior, configuration defaults, source-script commands and CLI entry points are preserved. Installing the optional MCP package does not change or enable a scheduled job. This release does not include a hosted ChatGPT plugin, HTTP server or PyPI/registry publication.

## 0.2.0 — 2026-11-02

- Add installable CLI commands and configurable JSON schedules, weekday overrides, work targets, extra minutes, lunch minimums and variance.
- Add configurable recovery for the most recent eligible empty workday and unfinished past days.
- Preserve recorded signs and protect holidays, absences, accepted/locked records, pending edits and paused dates in automatic recovery.
- Improve credential-file handling, token validation, request timeouts and documentation.

## Initial implementation

- Provide scripts for Woffu backfilling and scheduled clock-in/out.
