# CineVault Offline Subtitle Pipeline

This project creates English and Spanish sidecar subtitles for the CineVault
movie and TV catalogs without modifying video files or replacing existing
subtitles.

## Processing order

For each video, the pipeline checks sidecar files and embedded text subtitle
streams before running speech recognition.

1. Keep every existing `.srt` file unchanged.
2. Extract a matching embedded English or Spanish text subtitle when available.
3. Reuse and locally translate an existing English/Spanish SRT when only the
   other language is missing.
4. Otherwise extract one 16 kHz mono audio file to local scratch and run
   `faster-whisper` `large-v3` on the RTX 4090.
5. For Spanish audio, create native Spanish with `transcribe` and English with
   Whisper `translate`. For other audio, create the original/English track and
   translate English to Spanish with the locally installed Argos model.
6. Optionally merge conservative non-dialogue labels such as `[Music]`,
   `[Screaming]`, and `[Explosion]`, using the same approach tested on *Speed*.
7. Validate timestamps and write each final SRT atomically next to the video.

Generated files use `.en.whisper.srt` and `.es.whisper.srt`. Existing subtitles
are not overwritten. CineVault recognizes the language from the filename.

## Low-impact design

- Discovery walks directory entries, then probes only videos that still need a
  language. It never hashes or reads entire videos.
- A single worker extracts audio once from the share to local scratch. Whisper,
  translation, event detection, logs, and SQLite state remain local.
- Mount identity and free-space guards prevent accidental processing against an
  unavailable or incorrectly mounted share.
- Files must be unchanged for 30 minutes before entering the queue.
- The worker backs off while HandBrake/FFmpeg/transcode jobs are active or GPU
  use is above the configured threshold.
- Atomic rename publishes only fully validated SRT files.
- SQLite makes discovery and processing resumable across restarts.
- A systemd timer discovers new files every six hours. A daily full walk catches
  files missed by timestamps or interrupted scans.

This is intentionally polling rather than `inotify`: remote CIFS/NFS mounts do
not reliably deliver filesystem events to WSL.

## Commands

```bash
./bin/subtitle-pipeline scan
./bin/subtitle-pipeline status
./bin/subtitle-pipeline worker
./bin/subtitle-pipeline run-once
```

The installer creates user services:

- `cinevault-subtitle-worker.service`: one resumable worker
- `cinevault-subtitle-scan.timer`: new-file discovery every six hours

Logs and the queue database live under
`~/.local/state/cinevault-subtitles/`. Temporary audio lives under
`~/.cache/cinevault-subtitles/` and is removed after every job.

## Webex updates

The pipeline uses the existing `/mnt/c/Data/send_webex_notification.py` helper.
It reports scan start/completion, worker start, periodic completed/failed/queued
counts, individual failures, and an all-work-complete summary. Tokens remain in
the existing secret file and are never copied into this project or Git.

## Installation

```bash
./install.sh
```

The installer creates a private Python environment and installs `faster-whisper`
and `argostranslate`. Install the English-to-Spanish Argos package once with:

```bash
./bin/install-translation-models
```

CUDA libraries supplied by WSL are used by CTranslate2. Model files are cached
locally after their first download. Internet access is not required for normal
catalog processing after models are cached.

## Operational cautions

The first catalog run may take days or weeks depending on the number and length
of missing tracks. Do not increase beyond one worker unless scratch I/O, GPU
contention, share load, and atomic-output behavior have been re-evaluated. Review
the first several results in CineVault before leaving the full worker unattended.

