# Contributing

Thanks for your interest in WifiManager.

## Setup

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests --select E,F,W,I --ignore E501
```

Qt tests run off-screen. Tests that need the real Windows PowerShell or DPAPI are skipped on other systems.

## Before opening a pull request

- Run the tests and the linter, and make sure both pass.
- Keep a change focused: one pull request, one idea.
- Add a test for every behaviour you change. Most logic (rules, usage, firewall, service) is testable without Windows
  thanks to simulated PowerShell and firewall engines.
- **Never launch a program without `creationflags=CREATE_NO_WINDOW`.** Without it, a console window flashes when the app
  runs without a console. A test scans the whole source and fails if a launch lacks the flag.
- Keep secrets out of command lines, logs and files: the Wi-Fi password travels in environment variables and is stored
  with DPAPI.
- Do not weaken the safety rules in [SECURITY.md](SECURITY.md) (a read error never unblocks, dynamic firewall session,
  protected addresses) without discussing it in an issue first.

## Testing on a real hotspot

Changes to the firewall or to the hotspot calls cannot be fully proven by the simulated tests. If you touch them, say in the
pull request how you checked it on a real Windows PC (for blocking: a device that stops answering a ping, then answers again).

## Security problems

Report them privately, as described in [SECURITY.md](SECURITY.md), not in a pull request.

## Commit messages

Short and in the imperative mood, for example `Add a delay between service restarts`.
