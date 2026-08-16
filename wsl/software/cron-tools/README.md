# Cron Tools

Use these helpers to inspect CineVault-related cron entries.

```bash
./show_all_cinevault_cron.sh
```

The transcode orchestrator installs its own cron entries from:

```bash
../transcode-orchestrator/cron/install_transcode_cron.sh
```

The intended pattern is:

- `@reboot`: start the watchdog after a reboot.
- Hourly: validate queues, recover stale state, and send one WebEx status.

The watchdog itself exits after one pass; it is not a forever loop.
