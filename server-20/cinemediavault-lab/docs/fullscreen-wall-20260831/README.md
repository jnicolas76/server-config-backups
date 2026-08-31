# CineVault Full Screen Wall — 2026-08-31

Adds a **Full Screen Wall** button to `/wall`. A direct user tap requests
fullscreen for the entire four-tile grid. All four videos remain visible in a
2x2 layout and continue playing. The on-wall X, Android Back gesture/button, or
desktop Escape restores the normal page.

## Files

- `cinemediavault-lab-5000.py` — deployed canonical source
- `cinemediavault-lab-5000.before-fullscreen.py` — immediate rollback source
- `CLAUDE-HANDOFF.md` — design analysis and device limitations

## Live deployment

- Host: `192.168.1.20`
- Canonical root: `/home/jnicolas/cinemediavault-lab`
- URL: `https://192.168.1.20:5000/wall`
- Rollback: `cinemediavault-lab-5000.py.bak-fullscreen-wall-20260831-060216`

The standard Fullscreen API must be invoked directly from the button tap.
Android Chrome should hide browser chrome. APK support depends on its WebView
fullscreen configuration; test the existing per-tile fullscreen button first.
