# CineMediaVault Lab Installer

## HLS Compressed Download Size Slider

When an asset is set to HLS mode, the detail page shows a compressed download size slider. The slider starts from the configured default ratio and can be adjusted per movie, episode, or season before pressing Download. Direct mode downloads the original file. HLS mode queues a compressed mobile-friendly MP4 using the selected source-size percentage. Each selected percentage uses a separate cache key so multiple target sizes do not collide.

Relevant settings in cinemediavault-lab.env:

`env
MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO=0.40
MOBILE_DOWNLOAD_MAX_OUTPUT_RATIO=0.98
MOBILE_DOWNLOAD_MIN_EPISODE_MB=8
`

