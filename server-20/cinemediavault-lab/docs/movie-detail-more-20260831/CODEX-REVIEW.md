# Codex review and final adjustments

Claude correctly identified that `/movie/<id>` is rendered by the imported
`media_download_server.py` module and implemented the consistent More bottom
sheet there.

Before deployment, Codex added two corrections that were omitted from the
candidate:

- Replaced the remaining ambiguous circular start-over icon with a visible,
  labeled Restart button matching the canonical player.
- Wired the regular detail page's Mark Watched/Mark Unwatched control to the
  existing authenticated `/api/watch/watched` endpoint. Previously that
  control only linked back to `/movies` and did not change watch state.

The final deployed module therefore differs from Claude's original
`apply.patch`. Use `FINAL-DEPLOYED.patch` in the server post-change backup as
the authoritative complete change.
