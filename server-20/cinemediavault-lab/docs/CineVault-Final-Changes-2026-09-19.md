# CineVault Final Changes - 2026-09-19

This handoff records the production changes completed collaboratively through Codex, Kiro, and Claude for CineMedia Vault on server `192.168.1.20`.

## Weekly Guide Magazine

- Weekly Monday-Sunday PDF generation is active.
- The illustrated CineMedia Vault Guide masthead is restored and resolved from the application root so cron, admin, and manual runs use the same asset.
- Cover art is selected from scheduled, recent content while avoiding recently repeated covers.
- Cover includes the featured title and top-billed cast.
- The magazine includes correct "Also Featuring" posters, actor portraits and biographies, new-on-demand selections, sourced news summaries, and entertainment coverage.
- Movies and television are separated into explicit sections:
  - `MOVIES THIS WEEK`, followed by chronological Monday-Sunday movie grids.
  - `TELEVISION THIS WEEK`, followed by chronological Monday-Sunday television grids.
- Each day retains midnight-noon and noon-midnight listing pages.
- Current generated issue has 44 pages.
- Issues are sequentially archived under `/media/jnicolas/Expansion/CineGuideMagazine`.
- Current issue at completion: `CineMedia-Vault-Guide-Issue-010-2026-09-21.pdf`.
- `/weekly-guide` redirects to a unique issue URL such as `/weekly-guide.pdf?issue=CineMedia-Vault-Guide-Issue-010-2026-09-21.pdf`.
- PDF responses use issue-specific filenames and `no-store`/`no-cache` headers to prevent Chrome from reopening an old issue.

## Actor Metadata and Play Cards

- The low-priority TMDB people backfill writes portraits, biographies, birth information, departments, character names, and media credits to the production sidecar cache.
- Backfilled records become available live; no later merge is required.
- Movie and television play pages now contain an always-visible, horizontally scrollable Cast & Crew strip.
- Each cast card displays:
  - Circular portrait.
  - Actor's real name.
  - Character played in that movie or television show.
  - A neutral placeholder while a portrait is pending.
- Cards link to the existing actor details/search view.
- Television episodes use show-level cast credits.
- Backfill runs with `nice` and idle I/O scheduling to protect interactive CineVault performance.

## Cine News

- Broadcast target is capped at approximately 35 minutes.
- Weather appears exactly twice per broadcast: a primary forecast and one short update.
- Sports items are consumed once per edition instead of repeating through modulo logic.
- News, sports, entertainment, markets, traffic, and two weather checks remain part of the broadcast.
- Each of the 24 fictional staff members has a stable, unique Kokoro English voice.
- Staff portraits are used during the intro and staff presentation.
- The local library copy of Linkin Park's "What I've Done" is mixed only beneath the approximately 20-second intro video, beginning at the start of the song and fading out with the intro.
- It is not used as a bed beneath the rest of the broadcast.
- A new vertical `CineMedia Vault SPORTS & NEWS` poster is installed at `/home/jnicolas/cinemediavault-lab/static/cine-news-sports-poster.png`.
- Cine News guide metadata returns that poster, a program summary, TV-G rating, and current year.

## Production and Validation

- Primary application: `/home/jnicolas/cinemediavault-lab` on `.20`.
- Cine News tooling: `/home/jnicolas/cinevault-genchannel`.
- Weekly magazine tooling: `/home/jnicolas/cinemediavault-lab/cine-guide-magazine`.
- Application health was verified at `https://192.168.1.20:5000/` after deployment.
- Magazine was visually rendered and inspected for cover, dividers, listing alignment, and inserts.
- Current production application process was reduced to one instance after each restart.
- Pre-cast-card application backup: `/home/jnicolas/cinemediavault-lab/backups/cinemediavault-lab-5000.py.pre-cast-cards-20260919`.

## Source Files Backed Up

- `cinemediavault-lab-5000.py`
- `virtual_channels.py`
- `weekly_guide.py`
- `tmdb_people.py`
- `cache_tmdb_people.py`
- `start_tmdb_people_backfill.sh`
- Weekly Guide generator, launcher, README, and masthead.
- Cine News generator, renderer, staging/promote scripts, dry-run documentation, and guide poster.

## Operational Notes

- Do not store TMDB API secrets, user passwords, session keys, databases, downloaded actor caches, or licensed music in Git.
- The Git backup contains code, documentation, scripts, and generated branding assets only.
- The live TMDB people database and image cache remain production data and are backed up through the server's operational backup process.
