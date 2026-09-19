# CineMedia Vault Weekly Guide

Generates a static, non-clickable PDF magazine every Monday from the existing SQLite catalog and virtual-channel schedule. It does not scan the media library and does not modify playback or guide data.

The issue contains a poster-led cover with top-billed cast, a cover story with video stills, weekly editorial picks, and Monday-through-Sunday movie/TV grids in 12-hour sections. The output is atomically published as `weekly-guides/CineMedia-Vault-Guide-Latest.pdf`.

Run manually:

```bash
/home/jnicolas/cinemediavault-lab/cine-guide-magazine/generate_weekly_guide.sh
```

The production cron runs Mondays at 4:35 AM after schedules are available. The authenticated `/weekly-guide` endpoint serves the latest issue inline or for download.
