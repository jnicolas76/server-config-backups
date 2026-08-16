CineVault / BookVault / Transcode Troubleshooting Guide
=======================================================

Last updated: 2026-07-23

This file is a running notes/troubleshooting guide for the media tools built around
CineVault, BookVault, comics, games, and transcode jobs.


Important Hosts and Paths
-------------------------

Main CineVault server:
  Host: 192.168.1.20
  User: jnicolas
  Main direct-play port: 8093
  HLS/transcode test port: 8096
  Main server script: /home/jnicolas/media-library-server.py
  HLS/test server script: /home/jnicolas/media-library-server-hls.py
  Main log: /home/jnicolas/media-library-logs/server.log
  HLS log: /home/jnicolas/media-library-hls-logs/server.log

BookVault:
  Host: 192.168.1.20
  Port: 8111
  Script: /home/jnicolas/bookvault/bookvault_server.py
  Book root: /home/jnicolas/Data4/Books
  Log: /home/jnicolas/bookvault/bookvault.log

Local WSL / Windows shared data:
  Local WSL DATA path: /mnt/c/DATA
  Windows DATA path: C:\DATA
  Movies NFS/CIFS mount from local WSL: /mnt/nfs-share-movies
  Books on mounted Movies share: /mnt/nfs-share-movies/Books


Useful SSH Command
------------------

From local WSL:

  ssh -i /home/jnicolas/.ssh/id_rsa jnicolas@192.168.1.20


CineVault: 404 When Pressing Play
---------------------------------

Symptom:
  Pressing Play opens a URL like this and returns 404:

    /player/movie/582?play=1
    /player/tv/525?play=1

Cause:
  The CineVault page and server script are out of sync. This happened when the main
  direct-play server on port 8093 had newer routes, but the HLS/test server on port
  8096 was still running an older script that did not know about /player/movie/... or
  /player/tv/...

How to confirm:

  ssh jnicolas@192.168.1.20
  curl -I "http://127.0.0.1:8093/player/movie/582?play=1"
  curl -I "http://127.0.0.1:8096/player/movie/582?play=1"

Expected:
  HTTP 200

Bad:
  HTTP 404

Fix:

  ssh jnicolas@192.168.1.20
  cp /home/jnicolas/media-library-server.py /home/jnicolas/media-library-server-hls.py
  pkill -f "media-library-server-hls.py"
  nohup python3 /home/jnicolas/media-library-server-hls.py --host 0.0.0.0 --port 8096 > /home/jnicolas/media-library-hls-logs/server.log 2>&1 &

Verify:

  curl -I "http://127.0.0.1:8096/player/movie/582?play=1"
  curl -I "http://127.0.0.1:8096/player/tv/525?play=1"

Long-term recommendation:
  Avoid maintaining two separate Python server files. Run the same server code with
  different ports/configuration so 8093 and 8096 do not drift apart.


CineVault: Movie File Missing / 404
-----------------------------------

Symptom:
  Browser says:

    Error code: 404
    Message: Movie file missing.

Cause:
  The catalog/cache has a movie entry, but the underlying file was moved, renamed,
  deleted, or replaced after the last scan.

Fix:
  Press the CineVault Scan button, or run the refresh script on 192.168.1.20.

Useful checks:

  ssh jnicolas@192.168.1.20
  tail -80 /home/jnicolas/media-library-logs/server.log
  ls -lh "/path/to/movie/from/log"


CineVault: Scan Button Error
----------------------------

Symptom:
  Scan button starts but throws an error or does not refresh new posters/media.

Likely causes:
  1. Refresh script path is wrong.
  2. A scan is already running.
  3. NFS/CIFS mount is unavailable or slow.
  4. TMDb/network request failed.

Useful checks:

  ssh jnicolas@192.168.1.20
  curl "http://127.0.0.1:8093/api/full-scan-status"
  tail -80 /home/jnicolas/media-library-refresh-logs/cron.log
  ls -lt /home/jnicolas/media-library-refresh-logs | head


CineVault: Continue Watching Shows Items Never Watched
------------------------------------------------------

Symptom:
  Items appear under Continue Watching even though they were not intentionally played.

Cause:
  Continue Watching is saved in the browser local storage. If something briefly played
  or the page auto-opened the player, the browser may have saved progress.

Fix from browser:
  Use Mark Watched, or clear site data for CineVault in that browser.

Design fix added:
  Continue Watching should only show items with real progress, not just opened detail
  pages. Mark Watched should remove the item immediately without a full page refresh.


CineVault: Wrong Poster or Wrong TMDb Match
-------------------------------------------

Symptom:
  Movie/show uses the wrong TMDb metadata or poster.

Fix:
  Use the Fix Match option on the movie/show detail page.

Expected behavior:
  Fix Match searches TMDb and lets you pick another result.
  Poster upload should also be available when a local custom poster is preferred.

Common example:
  "The Stand" mini-series from the 1990s was accidentally matched to "Stand by Me".


CineVault: Missing Poster
-------------------------

Symptom:
  Poster says "No Poster".

Fix:
  Press Scan. The scan should refresh missing posters and update local metadata.

If still missing:
  Use Fix Match or upload custom poster art.


CineVault: Direct Play vs HLS
-----------------------------

Direct play:
  Port 8093 is the main direct-play experience. It streams the original file.

HLS/transcode testing:
  Port 8096 is the test instance for HLS/transcode behavior.

Known issue:
  Browser-based real-time transcode/HLS can be laggy on large files. Direct play is
  usually preferred unless the file format does not play in the browser.

Jellyfin-inspired HLS update:
  Port 8096 now runs the same CineVault UI but with:

    CINEVAULT_PLAYBACK_MODE=hls
    HLS_ENCODER=auto
    HLS_VIDEO_BITRATE=4500k
    HLS_VIDEO_MAXRATE=4500k
    HLS_VIDEO_BUFSIZE=9000k
    HLS_PREBUFFER_SEGMENTS=3

  The server prefers hardware H.264 through VAAPI when /dev/dri/renderD128 is
  available and writable. If hardware access is not available, it falls back to
  software libx264.

Why this helps:
  Jellyfin avoids transcoding unless needed, then uses FFmpeg plus hardware
  acceleration where possible. CineVault 8096 now follows that same basic idea:
  keep 8093 as direct play, and use 8096 for HLS transcoding with a hardware encoder
  and a short segment prebuffer.

How to confirm HLS mode is working:

  curl -I "http://127.0.0.1:8096/player/movie/<MOVIE_ID>?play=1"
  curl -I "http://127.0.0.1:8096/hls/movie/<MOVIE_ID>/index.m3u8"
  pgrep -af ffmpeg

Healthy signs:
  The player page returns HTTP 200.
  The HLS playlist returns Content-Type application/vnd.apple.mpegurl.
  pgrep shows ffmpeg using h264_vaapi or libx264.

Check FFmpeg logs:

  find /tmp/cinevault-hls -maxdepth 2 -name ffmpeg.log -printf '%T@ %p\n' | sort -nr | head
  tail -80 /tmp/cinevault-hls/<STREAM_FOLDER>/ffmpeg.log

Useful restart:

  pkill -f "media-library-server-hls.py"
  CINEVAULT_PLAYBACK_MODE=hls HLS_ENCODER=auto HLS_VIDEO_BITRATE=4500k HLS_VIDEO_MAXRATE=4500k HLS_VIDEO_BUFSIZE=9000k HLS_PREBUFFER_SEGMENTS=3 nohup python3 /home/jnicolas/media-library-server-hls.py --host 0.0.0.0 --port 8096 > /home/jnicolas/media-library-hls-logs/server.log 2>&1 &

Hardware access:
  The jnicolas user should be in the render and video groups:

    sudo usermod -aG render,video jnicolas
    id jnicolas

  A new login/session may be needed for group membership to apply.

HLS multi-client issue:
  If client 1 starts a movie while FFmpeg is still creating HLS segments, and client 2
  or 3 opens the same movie later, the browser may treat the growing playlist like a
  live stream. Symptoms:

    - Runtime appears to grow over time.
    - Later clients start farther into the movie.
    - Later clients cannot rewind to the true beginning.

  Fix added:
    - hls.js is configured with startPosition: 0.
    - CineVault forces new HLS clients to start at the beginning.
    - New FFmpeg HLS playlists include hls_playlist_type event.
    - Old generated HLS caches should be removed after this change so they regenerate
      with the new behavior.

  Clear stale HLS cache:

    pgrep -af '/usr/bin/ffmpeg.*cinevault-hls'
    rm -rf /tmp/cinevault-hls/movie-*

  Tradeoff:
    During live HLS generation, the browser's displayed duration can still grow until
    FFmpeg finishes the whole movie. That is normal for on-demand transcoding. Once the
    HLS cache reaches EXT-X-ENDLIST, duration becomes fixed and all clients should be
    able to seek normally.

Runtime showing growing cache instead of movie length:
  A later fix changed 8096 to serve a virtual VOD playlist for index.m3u8. The server
  gets the real source duration with ffprobe, then returns a full VOD manifest with:

    #EXT-X-PLAYLIST-TYPE:VOD
    #EXT-X-ENDLIST

  This makes the browser see the real movie runtime immediately instead of the growing
  number of generated cache segments. FFmpeg still generates the actual .ts segments
  in the background.

  Important behavior:
    - Runtime should show the full movie length immediately.
    - Starting clients should begin from segment 0.
    - Seeking to a future segment that has not been generated yet may wait/stall until
      FFmpeg catches up.

  Config:

    HLS_VIRTUAL_VOD=1
    HLS_SEGMENT_WAIT_TIMEOUT=120

Restart button resumes instead of starting over:
  Cause:
    The browser/HLS player can reapply the saved resume position after the restart
    button sets currentTime to 0, especially while HLS metadata is still attaching.

  Fix added:
    - Restart deletes the saved Continue Watching entry for that item.
    - Restart sets a force-start-from-zero flag.
    - hls.js is told to startLoad(0).
    - CineVault seeks to 0 immediately, then retries shortly after metadata/player
      attach completes.
    - Progress saving is paused during that restart window so it cannot re-save the
      old resume time.


CineVault: Mobile Address Bar Still Visible During Playback
-----------------------------------------------------------

Symptom:
  On Android Chrome, the movie starts playing but the browser address bar remains
  visible at the top.

Cause:
  Mobile browsers generally do not allow a web page to hide the address bar on page
  load. Fullscreen must be triggered by a user gesture, such as tapping Play or tapping
  the video. If the URL opens directly with ?play=1, the page can autoplay/start the
  player, but the browser may block true fullscreen because the page load itself is not
  considered a fresh user tap.

Fix added:
  CineVault now retries fullscreen when the user taps the player/video area and uses
  webkitEnterFullscreen where available. If the address bar remains visible after direct
  navigation to /player/...?...play=1, tap the video once.

Best user flow:
  Open a movie/show detail page and press the main Play button. That click gives the
  browser the user gesture it needs to enter fullscreen.

Long-term option:
  Install CineVault as a home-screen/PWA-style app with display fullscreen/standalone.
  That is the closest browser-supported way to make it feel like an app and avoid the
  normal browser chrome.


CineVault: AVI Files
--------------------

Symptom:
  AVI movies download but do not play in browser.

Cause:
  Many browsers do not support AVI playback directly.

Fix:
  Transcode AVI files to browser-friendly H.265/H.264 MP4/MKV according to the active
  transcode profile.


Transcode Orchestrator Notes
----------------------------

Expected behavior:
  1. Copy source file local if needed.
  2. Transcode with HandBrake/ffmpeg profile.
  3. Move finished encoded file back to original folder immediately after each file.
  4. Move original file into COMPLETED/archive folder.
  5. Continue to next queue item.
  6. Send WebEx status updates.

Current movie guidance:
  H.265 movie target is based on duration, about 500 MB per hour.
  Example: a 3 hour movie should land around 1.5 GB.

Older movie guidance:
  Some earlier jobs targeted about 1.5 GB fixed output.

Copa guidance:
  Copa jobs were changed from 700 MB to about 1.5 GB output.
  Copa games are now over, so regular hourly orchestration does not need to check Copa.

WebEx updates:
  Hourly updates should report active transcode job, queue status, and self-healing
  actions.


Transcode: Completed Folder Cleanup
-----------------------------------

Before deleting completed originals:
  Confirm the encoded replacement exists in the original target folder.

Useful checks:

  find /path/to/COMPLETED -type f | wc -l
  du -sh /path/to/COMPLETED

Only clean completed/archive folders after verifying replacements.


BookVault: Spanish Books Import
-------------------------------

Spanish tab:
  BookVault has tabs for All, English, and En Espanol / En Español.

Spanish import source:
  /mnt/nfs-share-movies/Books/6694 ebooks epub spanish español castellano (2013).zip

Spanish destination:
  /mnt/nfs-share-movies/Books/En Español

Source ZIP archive location after successful import:
  /mnt/nfs-share-movies/Books/En Español/_source_zips

Import script created locally:
  /mnt/c/Users/jonat/Documents/p-i-ip/prep_spanish_books.py

Important behavior:
  Extracts only .epub and .pdf.
  Deduplicates by SHA256 against existing books.
  Moves the source ZIP into _source_zips after a clean import.

Known issue:
  Extraction directly to mounted Movies share can become slow or enter D-state
  uninterruptible I/O wait. If this happens, do not start multiple duplicate imports.
  Wait for I/O to recover or run future imports by extracting locally first, then rsyncing
  to the share.

Check status:

  ps -eo pid,etime,stat,wchan:24,cmd | grep prep_spanish_books
  ls -lt "/mnt/nfs-share-movies/Books/En Español" | head
  du -sh "/mnt/nfs-share-movies/Books/En Español"


BookVault: Reader Notes
-----------------------

Current behavior:
  Clicking a book opens it directly.
  Card still has Download.
  Reader menu has Back/Home and chapter selector.
  Top Download button was removed.

PDF behavior:
  PDFs are displayed inline.
  PDF covers are generated from the first page using pdftoppm.

EPUB behavior:
  EPUB covers are pulled from the book when available.


Comics / Game Servers
---------------------

Comics and games were added as top navigation entries in CineVault.

Games:
  NES, Sega, DOS, and MAME should use official-style logos where available.

Comics:
  Comic libraries are intended to live on the NFS Movies share, not local WSL, when
  possible.


Common Process Checks
---------------------

On 192.168.1.20:

  ps -eo pid,etime,cmd | grep -E "media-library|bookvault|python" | grep -v grep

Check ports:

  ss -ltnp | grep -E ":8093|:8096|:8111"

Restart CineVault main:

  pkill -f "media-library-server.py"
  nohup python3 /home/jnicolas/media-library-server.py --host 0.0.0.0 --port 8093 > /home/jnicolas/media-library-logs/server.log 2>&1 &

Restart CineVault HLS/test:

  pkill -f "media-library-server-hls.py"
  nohup python3 /home/jnicolas/media-library-server-hls.py --host 0.0.0.0 --port 8096 > /home/jnicolas/media-library-hls-logs/server.log 2>&1 &

Restart BookVault:

  pkill -f "bookvault_server.py"
  cd /home/jnicolas/bookvault
  nohup python3 bookvault_server.py --host 0.0.0.0 --port 8111 --book-root /home/jnicolas/Data4/Books > bookvault.log 2>&1 &


Mount / Share Troubleshooting
-----------------------------

If NFS/CIFS is slow or unavailable:

  df -h
  mount | grep -E "nfs|cifs|Data|Movies|TV"
  timeout 10 stat /mnt/nfs-share-movies
  timeout 10 stat /mnt/nfs-share-tvshows

D-state process:
  If a process shows STAT D, it is stuck in uninterruptible I/O wait. kill -9 usually
  will not remove it until the storage call returns. Fix the mount/storage issue first.

Example:

  ps -o pid,stat,wchan,cmd -p <PID>


How To Add New Lessons To This File
-----------------------------------

When a problem is fixed, add:
  1. Symptom
  2. Cause
  3. How to confirm
  4. Fix command
  5. Long-term prevention if known
