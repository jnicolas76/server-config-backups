# CineVault Library Recommendations and Refresh

Deployment date: 2026-08-30

## Production service

- URL: `http://192.168.1.20:8093`
- Combined application: `/home/jnicolas/cinevault-watch-8093.py`
- Movie module: `/home/jnicolas/media-download-library/media_download_server.py`
- Production movie/TV scan schedule: every 15 minutes through `/home/jnicolas/media-library-refresh.sh`

The combined application loads the movie module when it starts. Restart the combined application after changing the module.

## Recommendation behavior

Movie detail pages contain a **Related Movies** rail and as many as three **More with ACTOR** rails.

Related movies are selected only from movies already present in the local CineVault index. Page rendering makes no Internet or TMDB API request. It compares locally cached TMDB metadata using:

1. Shared genres.
2. Jaccard similarity between meaningful words in the two locally cached plot summaries.
3. TMDB rating, vote count, and title as ordering and tie-break fields.

Actor rails use the first three cast members in locally cached TMDB billing order. Other library titles for each actor are ordered by TMDB rating and then vote count.

Recommendation cards are deduplicated by TMDB ID. When no TMDB ID is available, normalized title plus release year is used. This prevents multiple file entries for the same movie from appearing twice.

## Refresh behavior

Production scans the media libraries every 15 minutes. Production reads its own current cache directly. CineVault Lab watches the production cache and synchronizes a stable refresh automatically, normally 30 to 75 seconds after the production scan finishes.

## Validation

```bash
python3 -m py_compile /home/jnicolas/media-download-library/media_download_server.py
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8093/api/homepage/status
ss -ltnp | grep ':8093'
```

Expected results are a successful compile, HTTP `200` for the status endpoint, and a Python process listening on port 8093.

## Restart

Stop only the combined production process, then use the normal stack launcher. The launcher leaves services with already-listening ports unchanged.

```bash
pid="$(pgrep -f '^python3 /home/jnicolas/cinevault-watch-8093.py' | head -1)"
test -z "$pid" || kill "$pid"
/home/jnicolas/start_everything.sh
```

Loading the 6,000-plus movie and 38,000-plus episode indexes can take several seconds. Confirm port 8093 after the loader completes rather than relying on the launcher's initial two-second check.

## Rollback

The pre-deployment backup created on the production server is:

`/home/jnicolas/media-download-library/media_download_server.py.bak-related-20260830-065137`

To roll back:

```bash
cp -a /home/jnicolas/media-download-library/media_download_server.py.bak-related-20260830-065137 \
  /home/jnicolas/media-download-library/media_download_server.py
pid="$(pgrep -f '^python3 /home/jnicolas/cinevault-watch-8093.py' | head -1)"
test -z "$pid" || kill "$pid"
/home/jnicolas/start_everything.sh
```

Do not add metadata caches, credentials, TMDB configuration, databases, posters, tokens, or media files to GitHub.
