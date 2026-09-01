// CineVault overlay for sites/tvpassport.com.
//
// The upstream parser discards three attributes that the station pages actually
// carry and that the CineVault DVR relies on: data-new_show (drives "New episodes
// only" series rules), data-episodeNumber, and data-year.  This wrapper delegates
// 100% of the real work to the untouched upstream config (renamed to
// tvpassport.com.base.js at image build time) and only re-attaches those fields.
//
// It is intentionally fail-safe: any problem here leaves the upstream result as-is.

const cheerio = require('cheerio')
const base = require('./tvpassport.com.base.js')

function attr($item, name) {
  return $item.attr('data-' + name) || $item.attr('data-' + name.toLowerCase()) || ''
}

function extras(content) {
  const $ = cheerio.load(content)
  return $('.station-listings .list-group-item').toArray().map(el => {
    const $item = $(el)
    const episode = parseInt(attr($item, 'episodeNumber'), 10)
    const year = parseInt(attr($item, 'year'), 10)
    return {
      isNew: attr($item, 'new_show') === '1',
      episode: Number.isFinite(episode) && episode > 0 ? episode : null,
      year: Number.isFinite(year) && year > 1880 ? year : null
    }
  })
}

module.exports = {
  ...base,
  async parser(ctx) {
    const programs = await base.parser(ctx)
    try {
      const extra = extras(ctx.content)
      // Only enrich when the two parses agree item-for-item; otherwise leave upstream alone.
      if (Array.isArray(programs) && extra.length === programs.length) {
        programs.forEach((program, i) => {
          if (extra[i].isNew) program.new = true
          if (extra[i].episode !== null) program.episode = extra[i].episode
          if (extra[i].year !== null && !program.date) program.date = extra[i].year
        })
      }
    } catch {
      // Enrichment is best-effort; never fail a grab because of it.
    }
    return programs
  }
}
