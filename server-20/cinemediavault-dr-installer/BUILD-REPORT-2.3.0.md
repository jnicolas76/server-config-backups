# CineMediaVault DR Installer 2.3.0 — build report

**Build date:** 2026-09-11  
**Target:** Ubuntu 22.04/24.04, systemd, x86_64 or aarch64  
**Clean acceptance VM:** Proxmox VM 104, `cinemediavault-dr-test`, Ubuntu
24.04.5, 4 vCPU, 4 GiB RAM, 80 GiB disk

## Release contents

- Current CineMediaVault application payload synchronized from the active `.20`
  instance.
- Virtual Movie and TV guide with current channel logos and barker playback.
- Admin buttons for full Movie/TV schedule regeneration and barker-preview
  generation.
- Browser and server-side duplicate-submit protection. One shared operation lock
  prevents overlapping schedule or barker jobs and reports progress in Admin.
- Current 37-second-segment combined barker generator.
- Current Movie, TV, Music, Live TV/DVR, Book Vault, subtitle/Whisper, speech,
  transcode, creator, emulator-module, backup, health-check, and metadata support.
- Secure first-run web setup. Media roots are entered at the **Where is your
  media?** page; no site-specific media location is embedded in the package.

## Safety

- No source media or rendered barker media is included.
- No live database is included.
- No password, API token, private TLS key, or default administrator is included.
- Source media mounts are read-only in generated service confinement.
- Destructive library transcoding remains disabled until deliberately armed.

## Verification

- All Python payload and installer sources compiled.
- Setup JavaScript passed lexical validation.
- The complete installer unit/integration/security suite passed: **232/232**.
- The live virtual-channel suite passed: **39/39** before deployment.
- The live `.20` service answered its health request after deployment.
- VM 104 was verified to contain only its Ubuntu OS filesystem and no media
  mounts before the package was copied to `/home/jnicolas`.

The installer is intentionally not executed on VM 104. Running
`sudo ./install.sh` from the extracted directory launches the first-run setup
page and asks for the media locations before installation proceeds.
