# Bazarr on server 192.168.1.134

Bazarr runs in the existing `/home/jnicolas/docker-compose.yml` stack and is available on the LAN at `http://192.168.1.134:6767`.

## Integration

- Docker network: the compose-managed `jnicolas_default` network shared with Radarr and Sonarr
- Radarr: `http://radarr:7878`
- Sonarr: `http://sonarr:8989`
- Movies: host `/mnt/nfs-share-movies` mounted as `/movies`
- TV: host `/mnt/nfs-share-tvshows` mounted as `/tv`
- Persistent configuration: host `/home/jnicolas/bazarr` mounted as `/config`

The setup script reads the existing API keys locally on server 134 and submits them to Bazarr without printing or storing them in this repository.

## Subtitle policy

- Desired languages: English and Spanish
- Existing embedded subtitles count as present
- Automatic subtitle upgrades are disabled to preserve known-good files
- Adaptive searching is enabled
- Minimum score: 90 for episodes, 75 for movies
- Credential-free providers initially enabled: Gestdown and TVSubtitles
- Files are stored alongside media using language-tagged filenames

The CineVault WSL scanner recognizes Bazarr sidecars. Missing Spanish can be translated locally from English, while media without a usable authored subtitle remains eligible for the Whisper fallback.

## Commands

```bash
docker compose -f /home/jnicolas/docker-compose.yml up -d bazarr
/home/jnicolas/bazarr-configure.sh
/home/jnicolas/bazarr-assign-profiles.sh
/home/jnicolas/bazarr-search-tv.sh
/home/jnicolas/bazarr-status-134.sh
```

Do not commit `/home/jnicolas/bazarr/config/config.yaml`; it contains integration API keys.
