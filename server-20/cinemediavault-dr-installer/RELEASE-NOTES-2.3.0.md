# CineMediaVault disaster-recovery release 2.3.0

Built 2026-09-11 from the active CineMediaVault application on server `.20`.

This release adds guarded background Admin controls for rebuilding Movie and TV
virtual schedules and generating the six-hour barker preview. A shared
server-side operation lock rejects duplicate or overlapping manual jobs; the UI
also disables every schedule-operation button while a job is running and shows
live job status.

The package preserves the secure first-run setup website. On a clean Ubuntu
installation, `sudo ./install.sh` starts that page before CineMediaVault is
configured. Its **Where is your media?** step asks for Movies, TV, Music, Books,
Comics, Games, and Recordings locations. Every location may be left blank, so a
zero-media disaster-recovery install is supported and the paths can be added
later with `sudo cinevaultctl configure`.

The application payload was refreshed from the live service, including the
virtual-channel guide, 37-second barker generator, current channel artwork,
movie and TV services, and the full-book Book Vault audio workflow.

No source media, live databases, passwords, API keys, TLS private keys, or
generated barker video is included in this installer.
