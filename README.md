# woffu-helper

Configurable command-line tools for [Woffu](https://www.woffu.com):

- **`woffu-autosign`** signs in and out on a schedule, takes lunch breaks, and optionally recovers missing days or clock-outs.
- **`woffu-backfill`** previews and fills empty past workdays or closes unfinished days.

Python 3.9+ is required. These tools use the Woffu web app's internal API, which can change without notice. Record only hours you actually worked and follow your employer's time-recording policy.

## Install

```bash
git clone https://github.com/jamessi1989/woffu-helper.git ~/woffu-helper
cd ~/woffu-helper
python3 -m venv .venv
.venv/bin/python -m pip install .
source .venv/bin/activate
```

This installs `woffu-autosign` and `woffu-backfill`. On Windows, use the virtual environment's `Scripts` directory instead of `bin`.

The original commands still work from the checkout after `python3 -m pip install -r requirements.txt`:

```bash
python3 autosign.py --help
python3 backfill.py --help
```

To upgrade an installed copy after pulling changes, run `.venv/bin/python -m pip install .` again. A service running `python3 /path/to/autosign.py` reads the checkout directly.

## Credentials

1. Open your company's Woffu web app and sign in.
2. In browser DevTools, open **Network**, reload, and copy the JWT from a request's `authorization` header.
3. Save the host and token locally:

```bash
mkdir -p ~/.config/woffu
cp env.example ~/.config/woffu/env
chmod 600 ~/.config/woffu/env
$EDITOR ~/.config/woffu/env
```

`WOFFU_DOMAIN` accepts `yourcompany.woffu.com` or `https://yourcompany.woffu.com/`. `WOFFU_TOKEN` accepts the JWT with or without the `Bearer ` prefix. Expired tokens need renewing from the web app.

Both commands accept `--env-file /path/to/credentials`; `WOFFU_ENV_FILE` also selects a credential file. Explicit `WOFFU_DOMAIN` and `WOFFU_TOKEN` environment variables override file values. Credential files are read without changing the process environment, so separate accounts stay separate. **Never commit real credentials.**

## Configure automatic signing

For a new setup, copy and edit the example:

```bash
cp config.example.json ~/.config/woffu/config.json
$EDITOR ~/.config/woffu/config.json
woffu-autosign --show-config
woffu-autosign --dry-run
```

The example uses a 09:00 start, 13:00–13:30 lunch, eight worked hours, no extra time or random variance, and recovery switched off. Choose the schedule and recovery policy you want before enabling the timer.

Settings are read from `~/.config/woffu/config.json`, or from `--config PATH` / `WOFFU_CONFIG`. CLI options override the file, including weekday overrides. Relative `env_file` and `pause_dir` paths in JSON resolve beside the config file. `--show-config` prints the effective settings without loading credentials or accessing Woffu.

**Existing installations:** without a config file or new flags, the existing behavior is preserved: 07:00 earliest start, lunch around 13:00 with a 30-minute minimum, a 16:30 starting/resuming cutoff, eight worked hours plus 5–15 extra minutes, Monday–Friday, and both recovery modes over seven days. Existing flags, including `--close-hours`, remain supported.

| JSON setting / CLI option | Purpose | Default without a config |
|---|---|---|
| `start` / `--start` | Earliest clock-in, local `HH:MM`. | `07:00` |
| `lunch` / `--lunch` | `HH:MM-HH:MM`, or `none`. | `13:00-13:30` |
| `end` / `--end` | Latest starting/resuming cutoff; clock-out follows worked hours. | `16:30` |
| `work_hours` / `--work-hours` | Daily work target excluding breaks. `--close-hours` is an alias. | `8` |
| `extra_minutes` / `--extra-minutes` | Extra worked minutes; `0` disables. | `10` |
| `extra_variance` / `--extra-variance` | Random ± minutes around extra time, with a minimum of 1 when enabled. | `5` |
| `min_lunch_minutes` / `--min-lunch-minutes` | Minimum elapsed lunch, including delayed timer runs. | `30` |
| `lunch_variance` / `--lunch-variance` | Random 0..N minute delay for each lunch boundary. | `6` |
| `end_early_minutes` / `--end-early-minutes` | Maximum earlier cutoff. | `5` |
| `end_late_minutes` / `--end-late-minutes` | Maximum later cutoff. | `10` |
| `weekdays` / `--weekdays` | JSON list or comma-separated `mon,tue,...`. | Monday–Friday |
| `recovery` / `--recovery` | `off`, `close`, `fill`, or `both`. | `both` |
| `recovery_days` / `--recovery-days` | Past calendar days to inspect, 1–366. | `7` |
| `pause_dir` / `--pause-dir` | Directory for dated pause files and `pause-all`. | `~/.config/woffu` |
| `env_file` / `--env-file` | Credential file override. | `~/.config/woffu/env` |
| `request_timeout` / `--request-timeout` | HTTP timeout in seconds. | `20` |

Times use the computer's local timezone. Schedules must fit within one calendar day; overnight shifts are not supported. The selected weekdays must also be working days in Woffu's company calendar. Set variances to `0` for fixed timing; otherwise random choices are repeatable for each date.

### Different hours by weekday

Add `weekday_overrides` to the JSON file. Overrides support schedule fields, including start, lunch, work hours, extra time, and variance:

```json
{
  "work_hours": 8,
  "extra_minutes": 0,
  "weekday_overrides": {
    "fri": {"work_hours": 7, "lunch": "none"}
  }
}
```

Recovery uses the affected date's weekday settings. A CLI override such as `--work-hours 8` takes precedence on every day.

### Signing and recovery behavior

Each run reads today's actual entries and makes at most one live sign. Clock-out is due when the daily worked-time target is reached, excluding recorded breaks. Returning from lunch waits for both the scheduled return and the minimum break since the actual clock-out. Timer intervals can add a few minutes to the target.

Recovery runs before today's signing:

- `fill`: fill only the most recent eligible workday if it has no entries, using that day's configured start, lunch, and work target.
- `close`: inspect actual entries throughout the lookback window and add a final missing clock-out. This catches an open afternoon even when the daily summary still shows the lunch clock-out.
- `both`: perform both actions. `off`: make no historical recovery requests.

Completed days and existing signs stay unchanged. Recovery skips holidays, absences (approved or pending), paused dates, accepted or locked days, and pending edits. Ambiguous slot layouts are left alone. Network failures are logged and retried on later runs without blocking today's signing. Past changes may need approval under your company's settings.

Pause a date you did not work, keeping the file through the recovery window:

```bash
touch ~/.config/woffu/pause-2026-11-03
```

A pause for today still allows recovery of earlier dates. `pause-all` in the same directory stops everything. Use the configured `pause_dir` when running separate accounts.

Live signing needs the computer online and the job running. Recovery can repair the supported gaps when it comes back online; it does not infer missing lunch or afternoon entries in otherwise closed days.

## Run automatically

### systemd user timer (Linux)

The supplied service uses the virtual environment created above. Edit its executable path if you cloned elsewhere. The timer runs every five minutes, Monday–Friday, 07:00–20:55; **adjust its days and hours to cover your configured workweek and possible finish times**.

```bash
mkdir -p ~/.config/systemd/user
cp systemd/woffu-autosign.* ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/woffu-autosign.service
$EDITOR ~/.config/systemd/user/woffu-autosign.timer
systemctl --user daemon-reload
systemctl --user enable --now woffu-autosign.timer

systemctl --user list-timers woffu-autosign.timer
journalctl --user -u woffu-autosign -f
systemctl --user disable --now woffu-autosign.timer  # stop
```

### cron (Linux / macOS)

Use an absolute executable path and a range covering your schedule:

```cron
*/5 7-20 * * 1-5 /home/YOUR_USER/woffu-helper/.venv/bin/woffu-autosign >> /home/YOUR_USER/.cache/woffu-autosign.log 2>&1
```

Create the log directory first. Other schedulers, including Windows Task Scheduler, can invoke `woffu-autosign` every few minutes. Missed live timer runs are handled by the configured recovery policy.

## Backfill past days manually

Backfill has its own explicit CLI schedule; it does not load the autosign JSON file. Preview first:

```bash
# Empty workdays with the default 08:00–13:00 / 13:30–17:00 schedule
woffu-backfill fill --from 2026-01-01 --dry-run

# A single work period with weighted start times and no lunch
woffu-backfill fill --from 2026-07-01 --to 2026-09-30   --slots 07:00-15:00 --starts 07:00:45,08:00:40,08:30:15 --dry-run

# Past days missing a final clock-out
woffu-backfill close --from 2026-10-01 --hours 8 --dry-run
```

Remove `--dry-run` to write; confirmation is required unless you pass `--yes`. `--limit N` limits the number of days. `--skip DATE ...` excludes dates. `--variance N` adds ±N minutes to generated boundaries (default 10); `fill --min-lunch-minutes N` sets the minimum gap between work slots (default 30).

Filling skips days with existing entries, weekends, holidays, and approved/pending absences. Closing preserves recorded signs and adds only the missing final clock-out. The default end date is yesterday. Manual backfill plans re-roll their random times between preview and write; automatic recovery uses repeatable daily targets.

## Development

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -v
```

Tests use mocked APIs and require no Woffu credentials. CI runs the suite and installed CLI smoke checks on Python 3.9 and 3.13.

The client uses diary summaries at `/api/svc/core/diariesquery/users/{userId}/diaries/summary/presence`, reads slots by `diarySummaryId`, and writes slots by user and date. Sign times are local `HH:mm:ss` values. Existing signs retain `signStatus: 0`; new signs use `signStatus: 1` and `signType: 3`. Live signing uses the web app's toggle endpoint.

## License

MIT
