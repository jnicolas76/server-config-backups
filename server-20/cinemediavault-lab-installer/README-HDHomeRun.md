# CineMediaVault HDHomeRun Lab Module

The lab instance discovers HDHomeRun tuners on the local IPv4 network, stores
devices and channel lineups in the lab SQLite database, and exposes Live TV at
`/live-tv`.

## Use

1. Sign in to the lab as an administrator.
2. Open `http://SERVER:5000/live-tv`.
3. Select **Scan tuners** to refresh devices and channel lineups.
4. Select **Watch** for live playback, or **Add to wall** and choose wall 1-4.

The tuner MPEG-2/AC3 transport stream is converted to H.264/AAC HLS for browser
compatibility. Live HLS data uses the configured `HLS_CACHE_DIR` and is handled
by the existing HLS cache cleanup service.

## Database

The `hdhr_devices` and `hdhr_channels` tables store discovery and lineup data.
The `user_video_wall` table accepts `tuner` entries in addition to movies and
TV episodes. Existing wall selections are preserved during migration.

## Network

The scanner probes the server's local `/24` network and `192.168.1.0/24` for
`/discover.json`. The media server must be able to reach the tuner HTTP ports
80 and 5004.
