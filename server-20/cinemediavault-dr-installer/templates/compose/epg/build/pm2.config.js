// CineMediaVault EPG collector process definitions.
//
// Two long-lived processes: one serves the collected guide over HTTP, the other
// runs the grabber on a schedule. A third one-shot process performs the first
// collection at startup so a fresh install does not wait a full day for data.

const grab = process.env.SITES
  ? `npm run grab -- --sites=${process.env.SITES} ${
      process.env.CLANG ? `--lang=${process.env.CLANG}` : ''
    } --output=public/guide.xml`
  : 'npm run grab -- --channels=public/channels.xml --output=public/guide.xml'

const apps = [
  {
    name: 'serve',
    script: 'npx serve -- public',
    instances: 1,
    watch: false,
    autorestart: true
  },
  {
    name: 'grab',
    script: `npx chronos -e "${grab}" -p "${process.env.CRON_SCHEDULE}" -l`,
    instances: 1,
    watch: false,
    autorestart: true
  }
]

if (process.env.RUN_AT_STARTUP === 'true') {
  apps.push({
    name: 'grab-at-startup',
    script: grab,
    instances: 1,
    autorestart: false,
    watch: false,
    max_restarts: 1
  })
}

module.exports = { apps }
