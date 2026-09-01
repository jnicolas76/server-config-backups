# CineVault 14-Day EPG Extension

Implemented September 1, 2026.

## Outcome

- Collector: `cinevault-epg` on Docker host `192.168.1.134`
- Private feed: `http://192.168.1.134:3010/guide.xml`
- HDHomeRun: `192.168.1.213`, device `105935AB`, two tuners
- CineVault: `192.168.1.20:5000`
- Verified mappings: 86 Denver/Cheyenne OTA channels
- First complete collection: 1,204 channel-days, 22 MB XMLTV
- Extended programs accepted: 34,830
- Total cached programs after merge: 42,349
- Median guide coverage: 13.82 days
- Maximum guide coverage: 13.91 days

The original HDHomeRun guide remains authoritative for its approximately two-day window. TVPassport data from the iptv-org/epg collector is appended only after the HDHomeRun horizon. Every refresh revalidates overlapping program titles and times before a channel is extended. An unavailable, stale, malformed, mismatched, or incorrectly shifted feed leaves the HDHomeRun guide untouched.

## Locations

Docker host `.134`:

- `/home/jnicolas/cinevault-epg/docker-compose.yml`
- `/home/jnicolas/cinevault-epg/build/`
- `/home/jnicolas/cinevault-epg/public/channels.xml`
- `/home/jnicolas/cinevault-epg/public/guide.xml`

CineVault host `.20`:

- `/home/jnicolas/cinemediavault-lab/epg_extend.py`
- `/home/jnicolas/cinemediavault-lab/cinevault-data/epg-extend-map.json`
- `/home/jnicolas/cinemediavault-lab/hdhr-guide-cache.json`

## Operation

- Daily collection: 03:20 America/Denver
- Requested horizon: 14 days, the verified upstream limit
- Collector bind: private host address only, port 3010
- Restart policy: unless stopped
- Persistent output: `/home/jnicolas/cinevault-epg/public`
- Existing CineVault guide continues working during collection or collector failure

## Mobile DVR layout repair

The September 1 follow-up removed an accidental global search input from the DVR recording player, moved mobile playback immediately below its header, prevented the Home/Wall controls from overlapping the Live TV & DVR title, and made the DVR tab row horizontally scrollable without wrapping.

Backups:

- Pre-EPG CineVault backup: `/home/jnicolas/cinemediavault-lab/backups/20260901-epg-extend-pre`
- Pre-layout repair: `/home/jnicolas/cinemediavault-lab/backups/20260901-dvr-layout-pre`
- Post-layout repair: `/home/jnicolas/cinemediavault-lab/backups/20260901-dvr-layout-post`
