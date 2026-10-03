"""
Phish Time Machine — phish.net scraper
Run this locally to build the show database the app loads.

Usage:
    pip install requests
    export PHISHNET_API_KEY=xxxxxxxx      # free: https://phish.net/api/keys
    python scrape_phish.py                # every year, 1983 → this year
    python scrape_phish.py 1997 1998      # just these years (merged into
                                          # whatever's already cached)

Output:
    .phish-cache/<year>.json   raw API rows per year, so a re-run is cheap
    shows-data.json            the ERAS file index.html fetches at runtime

How it works:
    phish.net's v5 API returns one row per song performed, with the show,
    venue, set, position and — unlike setlists.net for the Dead — the
    transition mark (" > " segue, " -> " jam into, ", " stop). It also
    carries the jam chart flag, which the app uses instead of a hand-kept
    "highlight songs" list. One request per year, so the whole history is
    ~45 requests.

Audio isn't scraped: the app asks phish.in for a show's tracks at the
moment you hit play, so nothing here goes stale when a better source lands.
"""

import datetime, html, json, os, re, sys, time
from pathlib import Path
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT  = Path(__file__).parent
CACHE = ROOT / ".phish-cache"
OUT   = ROOT / "shows-data.json"
API   = "https://api.phish.net/v5"
DELAY_SEC = 1.0
UA = "PhishTimeMachine/1.0 (personal project; ~1 req/sec)"
FIRST_YEAR = 1983

# Eras the way the fan base counts them. 3.0 runs through the Feb 2020
# Riviera Maya shows; the post-pandemic return in 2021 opens 4.0.
ERA_MAP = [
    ("one",   (1983, 2000), "Phish 1.0",           "1983–2000",   "#C8102E"),
    ("two",   (2001, 2004), "Phish 2.0",           "2002–2004",   "#1F5FA8"),
    ("three", (2005, 2020), "Phish 3.0",           "2009–2020",   "#2E8B57"),
    ("four",  (2021, 9999), "Phish 4.0",           "2021–",       "#E07B16"),
]

DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SET_LABEL = {
    "1": "Set One", "2": "Set Two", "3": "Set Three", "4": "Set Four",
    "e": "Encore", "e2": "Second Encore", "e3": "Third Encore",
}
SET_ORDER = ["1", "2", "3", "4", "e", "e2", "e3"]

session = requests.Session()
session.headers["User-Agent"] = UA


def api_key():
    k = os.environ.get("PHISHNET_API_KEY", "").strip()
    if not k:
        sys.exit("Set PHISHNET_API_KEY first — get a free key at https://phish.net/api/keys")
    return k


def fetch_year(year, key):
    """All Phish setlist rows for one year. phish.net returns side projects
    (TAB, Mike Gordon, …) from the same endpoint, so filter to the band."""
    url = f"{API}/setlists/showyear/{year}.json"
    for attempt in range(5):
        r = session.get(url, params={"apikey": key}, timeout=40)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep((2 ** attempt) * 2)
            continue
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            raise RuntimeError(f"phish.net error for {year}: {j.get('error_message')}")
        return [row for row in j.get("data", [])
                if str(row.get("artist_slug", "")).lower() == "phish"
                or str(row.get("artistid", "")) == "1"]
    raise RuntimeError(f"phish.net: gave up on {year} after retries")


def strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n", s)).strip()


def era_for(year):
    for eid, (a, b), name, years, color in ERA_MAP:
        if a <= year <= b:
            return eid
    return ERA_MAP[-1][0]


def place(row):
    city, state, country = (row.get(k) or "" for k in ("city", "state", "country"))
    tail = state if country in ("USA", "United States", "") else country
    return ", ".join(p for p in (city, tail) if p)


def build_show(rows):
    rows = sorted(rows, key=lambda r: int(r.get("position") or 0))
    first = rows[0]
    date = first["showdate"]
    y, m, d = map(int, date.split("-"))

    by_set = {}
    jc = []
    for r in rows:
        sk = str(r.get("set") or "1").lower()
        if sk not in SET_LABEL:          # soundcheck and other oddities
            continue
        by_set.setdefault(sk, []).append(r)

    sets = []
    for si, sk in enumerate(k for k in SET_ORDER if k in by_set):
        songs = []
        for i, r in enumerate(by_set[sk]):
            title = html.unescape(r.get("song") or "").strip()
            mark = (r.get("trans_mark") or "").strip()
            is_last = i == len(by_set[sk]) - 1
            # Keep the band's own distinction: ">" segue, "->" jam into.
            if not is_last and mark in (">", "->"):
                title += " " + mark
            songs.append(title)
            if str(r.get("isjamchart") or "0") == "1":
                jc.append(f"{len(sets)}.{i}")
        sets.append({"lbl": SET_LABEL[sk], "songs": songs, "enc": sk.startswith("e")})

    return {
        "id": str(first["showid"]),
        "date": date,
        "dow": DOW[datetime.date(y, m, d).weekday()],
        "venue": html.unescape(first.get("venue") or "").strip(),
        "city": place(first),
        "tour": (first.get("tourname") or "").strip() or None,
        "notes": strip_html(first.get("setlistnotes")) or None,
        "url": first.get("permalink") or None,
        "jc": jc,
        "sets": sets,
    }


def scrape(years):
    CACHE.mkdir(exist_ok=True)
    key = api_key()
    for y in years:
        print(f"{y}…", end=" ", flush=True)
        rows = fetch_year(y, key)
        (CACHE / f"{y}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"{len({r['showid'] for r in rows})} shows")
        time.sleep(DELAY_SEC)


def merge():
    shows = []
    for f in sorted(CACHE.glob("*.json")):
        rows = json.loads(f.read_text(encoding="utf-8"))
        by_show = {}
        for r in rows:
            by_show.setdefault(r["showid"], []).append(r)
        for rs in by_show.values():
            s = build_show(rs)
            if s["sets"]:
                shows.append(s)
    shows.sort(key=lambda s: (s["date"], s["id"]))

    eras = []
    for eid, _, name, years, color in ERA_MAP:
        es = [s for s in shows if era_for(int(s["date"][:4])) == eid]
        if es:
            eras.append({"id": eid, "name": name, "years": years, "color": color, "shows": es})
    OUT.write_text(json.dumps(eras, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT.name}: {len(shows)} shows across {len(eras)} eras.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.isdigit()]
    if "--merge" not in sys.argv:
        years = [int(a) for a in args] or list(range(FIRST_YEAR, datetime.date.today().year + 1))
        scrape(years)
    merge()
