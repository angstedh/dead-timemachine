# Phish Time Machine 🍩

The Dead Time Machine, rebuilt for Phish. Same engine — show of the day,
"take me anywhere", browse by era/year, search, venue guide, song history,
lineup, runs and tour legs, this day in history — with Phish-specific data:

| | Dead version | Phish version |
|---|---|---|
| Setlists | setlists.net (scraped HTML) | **phish.net API v5** |
| Segues | cross-referenced from jerrygarcia.com | phish.net's own marks: `›` segue, `→` jam into |
| Highlights | hand-kept list of songs | **phish.net jam chart**, per performance |
| Audio | archive.org embed | **phish.in**, track-by-track player |
| Eras | Pigpen / Keith & Donna / Brent / Final | 1.0 / 2.0 / 3.0 / 4.0 |

It lives at `/phish/` on the same GitHub Pages site:
`https://YOUR-USERNAME.github.io/dead-timemachine/phish/`

## Load the shows (required — ships with no data)

```bash
cd phish
pip install requests
export PHISHNET_API_KEY=xxxxxxxx   # free key: https://phish.net/api/keys
python scrape_phish.py             # ~45 requests, one per year, about a minute
git add shows-data.json && git commit -m "phish archive" && git push
```

Re-run any time to pick up new shows (`python scrape_phish.py 2026` refreshes
just one year). Never commit your API key.

Optional, venue building history and photos from Wikipedia (reuses
`../scrape_venues.py`):

```bash
python scrape_venues.py && python scrape_venues.py --merge
```

## Caveats

- **Audio depends on phish.in allowing cross-origin requests from your site.**
  If it doesn't, the Play button falls back to a "listen on phish.in" link.
- The lineup's early dates (Page joining, Jeff Holdsworth leaving) are
  month-approximate, same as the Dead version's.
- "Bust-out" here means 100+ shows since the song's last appearance. That's
  this app's threshold, not an official phish.net one.

## Sources

- **phish.net** — setlists, transitions, jam chart, show notes
- **phish.in** — streaming audio
- **Wikipedia** — venue history/photos, and "this day in history"
