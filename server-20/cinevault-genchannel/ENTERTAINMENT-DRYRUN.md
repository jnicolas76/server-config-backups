# Cine News entertainment dry run

## Sources and usage

The registry contains Variety, The Hollywood Reporter, Deadline, Rolling Stone, Billboard, IGN, TMZ, NPR Pop Culture, and Google News Entertainment RSS. Every source is validated before use and the result is persisted in `entertainment-source-validation.json`. Broadcast narration uses attributed headline-level summaries only; it does not scrape or reproduce article bodies. Links and source names remain in the data manifest for audit. Redistribution remains marked `review` pending legal review of each publisher's RSS terms.

## Broadcast

`broadcast_generator_v3.py` retains weather, general/local news, sports, traffic, and markets and adds an Entertainment segment. The requested duration is clamped to 35 minutes; content targets 33 minutes to reserve time for the intro and close. The dry-run output is `output/broadcast-entertainment-dryrun-20260919.mp4`; it is never passed to either promotion script.

The fictional staff roster has 24 reusable JPEG portraits in `staff-portraits/`, plus six crew sheets. Each edition selects its normal crew and overlays that crew's sheet during the spoken intro. These are synthetic fictional likenesses and are not representations of real people.

## Magazine

The Weekly Guide keeps its cover, editorial, cast, on-demand, 28 schedule-grid pages, archive, history, and Latest PDF behavior. Sports pages were replaced with sourced Entertainment Report/Notebook pages. The resulting issue is 42 pages.

## Review and rollback

The proof video, magazine, and validation report are linked from `http://192.168.1.20:8901/`. Rollback files are under `/home/jnicolas/cinevault-backups/cinenews-entertainment-dryrun-20260919-135510/`. Restoring the backed-up generator/renderer/magazine generator reverts the code; staff assets are additive and may remain unused.
