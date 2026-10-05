# woffu-helper

Small Python scripts for [Woffu](https://www.woffu.com) time tracking:

- **`backfill.py`** fills past workdays that have nothing recorded, and closes days where you clocked in but never clocked out.
- **`autosign.py`** clocks you in and out automatically while your computer is on: clock in in the morning, out and back in for lunch, out at the end of the day.

These scripts use the same internal endpoints as the Woffu web app. Woffu doesn't document or support them, and they may change without notice.

> **Use responsibly.** In many countries (Spain's *registro de jornada*, for example) your time record is a legal document. Only record hours you actually worked, and check your employer's policy before automating anything.

## Requirements

- Python 3.9+ and `requests` (`pip install -r requirements.txt`)
- A Woffu account that signs in through the web app

## Setup

### 1. Get your token

1. Log in to your company's Woffu web app, for example `https://yourcompany.woffu.com`.
2. Open DevTools (F12), go to the **Network** tab, and reload the page.
3. Click any request to `/api/...` and copy the value of the `authorization` request header. Leave out the `Bearer ` prefix.

The token is a JWT. Your user ID and the expiry date are read from it. Tokens usually last months; when yours expires, the scripts say so and you copy a new one.

### 2. Save the config

```bash
mkdir -p ~/.config/woffu
cp env.example ~/.config/woffu/env
chmod 600 ~/.config/woffu/env
$EDITOR ~/.config/woffu/env      # set WOFFU_DOMAIN and WOFFU_TOKEN
```

You can also set `WOFFU_DOMAIN` and `WOFFU_TOKEN` as environment variables instead. **Never commit the token**: anyone who has it can sign in to Woffu as you.

## Backfilling past days

You don't need an agent for this; it's a normal command-line tool. Always do a `--dry-run` first: it lists every day it would change and the times it would write.

```bash
# Workdays since Jan 1 with nothing recorded, normal 8-17 day with lunch
python3 backfill.py fill --from 2026-01-01 --dry-run

# Summer hours, rotating the start between 07:00 (most days), 08:00 and 08:30.
# The end of the day moves with the start, so the day keeps its length.
python3 backfill.py fill --from 2026-07-01 --to 2026-09-30 \
    --slots 07:00-15:00 --starts 07:00:45,08:00:40,08:30:15 --dry-run

# Days you clocked in but forgot to clock out: keep the real signs and
# add a clock-out so the day totals ~8h
python3 backfill.py close --from 2026-06-01 --dry-run
```

Remove `--dry-run` to write. The script asks for confirmation unless you pass `--yes`. Use `--limit 1` to try a single day first.

| Option | Meaning |
|---|---|
| `--from`, `--to` | Date range. `--to` defaults to yesterday. |
| `--slots` | Work periods. Default `08:00-13:00,13:30-17:00`. |
| `--starts` | Rotate the start time, as `time:weight`. Breaks stay where they are. |
| `--variance` | Random ± minutes on every time (default 10). |
| `--skip` | Dates to leave untouched. |
| `--hours` | (`close`) Target hours for the day (default 8). |

The script **always skips** weekends, holidays, days with an approved or pending absence, and days that already have signs.

**Approvals:** depending on your company's settings, edits to past days may appear as *pending* until a manager approves them.

### Running it through an AI agent (optional)

The backfill is a CLI, but an agent can be useful for the parts that need judgment:
- working out which date ranges use which schedule,
- spotting odd days, such as gaps inside a leave period or signs recorded on days off,
- doing a dry run before every write.

Give the agent shell access in this repo and a prompt like:

```text
Use backfill.py in this repo to fill my Woffu diary from <DATE> to <DATE>.
My schedule was: <e.g. 07:00-15:00 in Jul-Sep, 08:00-13:00 + 13:30-17:00 otherwise>,
starting at 07:00 most days, sometimes 08:00 or 08:30.
1. Run `backfill.py fill ... --dry-run` for each period and show me the plan.
2. Flag anything odd (days inside an absence period, unexpected layouts).
3. Write one day with --limit 1, read it back, then the rest after I confirm.
4. Then run `backfill.py close --dry-run` for days missing a clock-out.
```

## Automatic clock-in/out

`autosign.py` is meant to run every few minutes on workdays. It doesn't store any state itself: each run reads what you've already recorded today from Woffu and makes **at most one** sign, the next one that's due. Running it twice, rebooting, or signing by hand won't create duplicates.

### Rules

| Woffu shows today | Time | Action |
|---|---|---|
| nothing | `--start` to end | clock in |
| clocked in, no lunch yet, started before lunch | lunch start to lunch end | clock out (lunch) |
| out for lunch | lunch end to end | clock in |
| clocked in | after end | clock out |
| anything else | – | nothing |

- **Defaults:** start 07:00, lunch 13:00–13:30, end 16:30. Lunch and end get a few minutes of random variance, fixed per day so every run agrees.
- **Skipped days:** weekends, holidays, days with an absence, and paused days.
- **Forgotten clock-outs:** if your computer was off at the end time, the next run closes that day by adding a clock-out at ~`--close-hours` (default 8h).
- **Pause a day** (sick, day off not yet in Woffu): `touch ~/.config/woffu/pause-2026-10-06`. Use `pause-all` to pause it indefinitely.
- **Check what it would do now:** `python3 autosign.py --dry-run`.

It only signs while your computer is **on and the job is running**. It records a full day whenever the machine is on, whether or not you're working, so use the pause file.

### Option A: systemd user timer (Linux, recommended)

Runs as your user, logs to the journal, and also runs shortly after you log in.

```bash
git clone https://github.com/<you>/woffu-helper ~/woffu-helper
mkdir -p ~/.config/systemd/user
cp ~/woffu-helper/systemd/woffu-autosign.* ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/woffu-autosign.service   # path + your times
systemctl --user daemon-reload
systemctl --user enable --now woffu-autosign.timer

systemctl --user list-timers woffu-autosign.timer   # next run
journalctl --user -u woffu-autosign -f              # logs
systemctl --user disable --now woffu-autosign.timer # stop
```

### Option B: cron (Linux / macOS)

```cron
*/5 7-17 * * 1-5  /usr/bin/python3 $HOME/woffu-helper/autosign.py --start 07:00 --end 16:30 >> $HOME/.cache/woffu-autosign.log 2>&1
```

Cron doesn't run jobs that were missed while the machine was asleep. That's fine here, because the next run catches up.

## How it works

| Purpose | Endpoint |
|---|---|
| Diary summaries (past days) | `GET /api/svc/core/diariesquery/users/{id}/diaries/summary/presence` |
| A day's slots | `GET /api/diaries/{diaryId}/workday/slots` |
| Write a day | `PUT /api/diaries/{diaryId}/workday/slots/self` |
| Today's live signs | `GET /api/svc/signs/v2/signs/slots` |
| Clock in/out now (toggle) | `POST /api/svc/signs/v2/signs/signrequest` |

Things that aren't obvious:
- The diary query returns at most 100 rows per page, so the scripts fetch in 31-day chunks.
- When writing a day, existing signs must be sent back with `signStatus: 0` to stay unchanged.
- New or edited signs need `signStatus: 1` and `signType: 3`, otherwise the server returns 204 but ignores them.

## License

MIT
