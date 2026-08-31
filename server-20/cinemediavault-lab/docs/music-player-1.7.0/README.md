# CineMediaVault 1.7.0 Music Player Upgrade

## Release

- Android package: `com.cinevault.companion`
- Version: `1.7.0` (`versionCode 27`)
- Previous rollback release: `1.6.1` (`versionCode 26`)
- Server: `192.168.1.20`, CineVault HTTPS port `5000`

## What changed

- Added a full-screen music player with large artwork, seek bar, elapsed/duration time, previous/play/next, queue, shuffle, repeat, and Cast controls.
- Added per-user server-side music state in `music_playback_state`, including track, position, queue, queue index, shuffle, repeat, and playing state.
- Browser playback uses the Media Session API for lock-screen/media controls where the browser supports it.
- The Android app adds `MusicPlaybackService`, a foreground media-playback service with a native MediaSession and notification controls.
- Android native playback retains queue and position in SharedPreferences and can restore after ordinary process recreation. Android force-stop remains an operating-system hard stop.
- Cast detection now recognizes both `<video>` and `<audio>` and assigns audio MIME types for music.
- The current 1.6.1 offline library, folder selection, season ZIP extraction, download metadata, and intro behavior were retained.

## Server files

- Application wrapper: `/home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py`
- Music module: `/home/jnicolas/cinemediavault-lab/music_module.py`
- Music database: `/home/jnicolas/cinevault-data/music.db`
- Pre-change backup: `/home/jnicolas/cinemediavault-lab/backups/music-player-full-pre-20260831-131500/`
- APK archive: `/home/jnicolas/cinemediavault-lab/apk/`

## API

- `GET /api/music/now-playing` returns the signed-in user's saved playback state.
- `POST /api/music/now-playing` saves the signed-in user's playback state.
- Queue input is validated and capped at 500 tracks.

## Install and rollback

Install `CineMediaVault-1.7.0-music-player.apk` over 1.6.1. It uses the same application ID and signing certificate, so Android accepts it as an upgrade. If rollback is needed, uninstall 1.7.0 and install `CineMediaVault-1.6.1-admin-download-fix.apk`; Android generally does not permit an in-place downgrade.

On first launch Android 13+ may ask for notification permission. Allow it so the music playback notification and lock-screen controls remain visible.

## Verification performed

- Python module compilation and backend playback-state tests passed.
- Extracted page JavaScript passed `node --check`.
- Live CineVault login endpoint returned HTTP 200 after deployment.
- Android Gradle build completed successfully.
- APK metadata verified as version 1.7.0 / versionCode 27.
- APK signature SHA-256 matches the 1.6.1 release: `92bdd81dec465207dcd6f69c94dfa3d60333b76e59dc98b10ccbbc85e9963e55`.
- APK SHA-256: `1efda976263eeb4fa5b4d4df9548c281389cdf9dd8d003e7055b88addcd10bf0`.

## Source

The source archive contains the reconstructed 1.6.1 feature set plus the 1.7.0 music additions. `MusicPlaybackService.java` contains the foreground player and `MainActivity.java` exposes the `CineVaultNativeMusic` JavaScript bridge.
