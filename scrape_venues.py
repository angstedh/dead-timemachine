"""
scrape_venues.py — venue history (built / renamed / demolished) + photos from Wikipedia.

None of this is in setlists.net, so this cross-references Wikipedia the same
way scrape_segues.py cross-references jerrygarcia.com for segues: resolve each
venue to an article (a curated map for names already verified for the photo
lookup in index.html, a live search for everything else), pull the infobox
out of the article's lead section, and grab a short intro plus a handful of
real photos off the page.

"Venue" here is the same identity index.html uses for the Venue Guide sheet —
name + city, since several buildings share a bare name ("Capitol Theater" is
Port Chester, NY *and* Passaic, NJ; "Fox Theater" is St. Louis *and* Atlanta).

Usage:
    pip install requests
    python scrape_venues.py          # resumable — safe to stop and re-run
    python scrape_venues.py --merge  # write venues-data.json from the cache
    python scrape_venues.py --retry-failed   # clear cache entries that found
                                              # nothing, so the next run retries them
    python scrape_venues.py --audit          # re-check cached *successes* against
                                              # the current rules (offline) and
                                              # clear any that now fail, so the
                                              # next run re-resolves just those

Output:
    .venue-cache/<key>.json   one file per venue, so a re-run costs nothing
    venues-data.json          the merged file index.html fetches at runtime
"""

import datetime, html, json, re, sys, time
from pathlib import Path
import requests

# Windows redirects stdout to a codepage that can't hold "→" etc., which was
# crashing the per-venue print (and getting mis-reported as a fetch failure,
# even though the venue's data had already been written) — force UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT  = Path(__file__).parent
DATA  = ROOT / "shows-data.json"
CACHE = ROOT / ".venue-cache"
OUT   = ROOT / "venues-data.json"
DELAY_SEC = 1.2
UA = "DeadTimeMachine/1.0 (personal project; respectful, ~1 req/sec)"
API = "https://en.wikipedia.org/w/api.php"

session = requests.Session()
session.headers["User-Agent"] = UA


def api_get(params, timeout=25):
    """GET against the Wikipedia API with backoff on 429/5xx — a plain
    session.get blew through the whole archive as silent failures the first
    time a burst of requests (from this and a couple of manual test calls)
    tripped Wikipedia's rate limiter."""
    last = None
    for attempt in range(5):
        r = session.get(API, params=params, timeout=timeout)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After", 0)) or (2 ** attempt) * 2
            last = requests.exceptions.HTTPError(f"{r.status_code} on attempt {attempt+1}, waiting {wait:.0f}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise last or RuntimeError("api_get: exhausted retries")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# Buildings the band played under more than one name — merged the way
# venueKey() in index.html already merges them for the venue stat guide.
VENUE_RENAME = {
    "Oakland Auditorium Arena": "Henry J. Kaiser Convention Center",
}

def venue_name(v):
    return VENUE_RENAME.get(v, v)

def venue_key(name, city):
    return slug(venue_name(name)) + "|" + slug(city)


# Exact copy of VENUE_WIKI from index.html — names already verified there for
# the live photo lookup. Keyed by the raw venue string (not venueKey) exactly
# like the JS map, so behavior matches what's already shipped; only kept
# name-only (not name+city) because that's what index.html already trusts.
CURATED_WIKI = {
    "Oakland Coliseum Arena": "Oakland Arena",
    "Winterland Arena": "Winterland Ballroom",
    "The Spectrum": "Spectrum (arena)",
    "Madison Square Garden": "Madison Square Garden",
    "Nassau Coliseum": "Nassau Veterans Memorial Coliseum",
    "Shoreline Amphitheatre": "Shoreline Amphitheatre",
    "Fillmore East": "Fillmore East",
    "Fillmore West": "Fillmore West",
    "Fillmore Auditorium": "The Fillmore",
    "Greek Theater, University Of California": "William Randolph Hearst Greek Theatre",
    "Henry J. Kaiser Convention Center": "Henry J. Kaiser Convention Center",
    "Oakland Auditorium Arena": "Henry J. Kaiser Convention Center",
    "Capital Centre": "Capital Centre",
    "Boston Garden": "Boston Garden",
    "The Omni": "Omni Coliseum",
    "Berkeley Community Theater": "Berkeley Community Theatre",
    "Hampton Coliseum": "Hampton Coliseum",
    "Warfield Theater": "The Warfield",
    "Red Rocks Amphitheater": "Red Rocks Amphitheatre",
    "Alpine Valley Music Theater": "Alpine Valley Music Theatre",
    "Providence Civic Center": "Providence Civic Center",
    "Hartford Civic Center": "Hartford Civic Center",
    "Capitol Theater": "Capitol Theatre (Port Chester, New York)",
    "Uptown Theater": "Uptown Theatre (Chicago)",
    "Boston Music Hall": "Boston Music Hall",
    "Robert F. Kennedy Stadium": "Robert F. Kennedy Memorial Stadium",
    "Barton Hall (Cornell University)": "Barton Hall",
    "Soldier Field": "Soldier Field",
    "Cal Expo Amphitheater": "Cal Expo",
    "Frost Amphitheater": "Frost Amphitheater",
    "Giants Stadium": "Giants Stadium",
    "Autzen Stadium": "Autzen Stadium",
    "Irvine Meadows Ampitheatre": "Irvine Meadows Amphitheatre",
    "Deer Creek Music Center": "Deer Creek Music Center",
}


# ─── resolve a venue to a Wikipedia title ─────────────────────────────────
def resolve_title(name, city):
    if name in CURATED_WIKI:
        return CURATED_WIKI[name]
    try:
        r = api_get({
            "action": "query", "list": "search",
            "srsearch": f"{name} {city}", "srlimit": 1, "format": "json",
        }, timeout=20)
        hits = r.json().get("query", {}).get("search", [])
        return hits[0]["title"] if hits else None
    except Exception:
        return None


# ─── infobox parsing ───────────────────────────────────────────────────────
TMPL_CLEAN = [
    (re.compile(r"<ref[^>]*>[\s\S]*?</ref>", re.I), ""),
    (re.compile(r"<ref[^>]*/>", re.I), ""),
    (re.compile(r"\{\{\s*(?:start date and age|start date)\s*\|(?:df=y\|)?(\d{4})[^}]*\}\}", re.I), r"\1"),
    (re.compile(r"\{\{\s*(?:small|nowrap|nobold|plainlist)\s*\|([^{}]*)\}\}", re.I), r"\1"),
    (re.compile(r"\{\{\s*ubl\s*\|([^{}]*)\}\}", re.I),
     lambda m: "; ".join(p.strip() for p in m.group(1).split("|") if p.strip())),
    (re.compile(r"\[\[[^\]|]*\|([^\]]*)\]\]"), r"\1"),
    (re.compile(r"\[\[([^\]]*)\]\]"), r"\1"),
    (re.compile(r"\{\{[^{}]*\}\}"), ""),   # any remaining simple template
    (re.compile(r"'''?"), ""),
    (re.compile(r"<br\s*/?>", re.I), "; "),
    (re.compile(r"<[^>]+>"), ""),
    (re.compile(r"\s+"), " "),
]

def clean(v):
    if v is None:
        return ""
    # Some infoboxes mix real Unicode dashes with literal HTML entities
    # ("2006&ndash;2019") in the same field — unescape first so a trailing
    # entity ";" can't be mistaken for a field/name separator downstream.
    v = html.unescape(v)
    for _ in range(3):   # nested templates need a couple passes
        for pat, repl in TMPL_CLEAN:
            v = pat.sub(repl, v)
    return v.strip(" ;,\n\t")


def find_infobox(wikitext):
    """First {{Infobox ...}} block, brace-balanced."""
    m = re.search(r"\{\{\s*Infobox", wikitext, re.I)
    if not m:
        return None
    start = m.start()
    depth, i = 0, start
    while i < len(wikitext) - 1:
        two = wikitext[i:i+2]
        if two == "{{":
            depth += 1; i += 2; continue
        if two == "}}":
            depth -= 1; i += 2
            if depth == 0:
                return wikitext[start:i]
            continue
        i += 1
    return None


# A free-text Wikipedia search for "<venue> <city>" sometimes lands on the
# wrong article entirely — a band member whose infobox happens to have a
# "years_active" field that reads like a building's operating years, the city
# itself, an unrelated album, the parent university instead of its specific
# arena. None of that is a venue, so none of its facts/photos/extract should
# ever be trusted. Caught by infobox *type*, since that's unambiguous even
# when the title itself looks plausible at a glance.
NON_VENUE_INFOBOX = re.compile(
    r"^(musical artist|musician|band|album|single|song|ep|person|writer|author|"
    r"journalist|scientist|engineer|officeholder|politician|military person|"
    r"criminal|comedian|actor|royalty|saint|pope|president|monarch|"
    r"settlement|country|islands?|government agency|company|organi[sz]ation|"
    r"university|school|college|educational institution|"
    r"film|television|television season|television episode|book|magazine|"
    r"video game|software|website|"
    r"military conflict|military unit|military operation|war|"
    r"sports team|national sports team|football (club|biography)|ice hockey player|"
    r"(ncaa|college|national) team season|season|olympic games|olympics|"
    r"language|ethnic group|ship|aircraft|automobile|weapon|"
    r"mountain|river|lake|protected area|airport|"
    r"park|garden|historical event|event|festival|election|"
    r"road|street|bridge|tunnel)\b",
    re.I,
)

# A one-off *game played at* a venue ("1982 McDonald's All-American Boys
# Game" for Rosemont Horizon) names and describes the venue in passing, which
# is exactly enough to fool both the word-overlap and city checks below —
# caught separately since the type string varies too much ("basketball game",
# "college football game", "tennis tournament"...) for a prefix match.
SPORTS_EVENT_INFOBOX = re.compile(r"\b(game|match|bout|fight|tournament|meet)\b", re.I)

def is_non_venue_type(itype):
    return bool(itype) and bool(NON_VENUE_INFOBOX.match(itype) or SPORTS_EVENT_INFOBOX.search(itype))

def infobox_type(wikitext):
    m = re.search(r"\{\{\s*Infobox\s+([^\n|{}]+)", wikitext, re.I)
    return m.group(1).strip() if m else None


# Second line of defense alongside the infobox-type gate: a search match whose
# infobox type isn't on the blocklist above (an "Infobox park" or "Infobox
# historical event" we hadn't thought to block, say) can still be a totally
# unrelated article. Not proof either way — just a sniff test — so it only
# gates *search*-resolved titles, never the hand-verified CURATED_WIKI ones.
NAME_STOPWORDS = {
    "the", "of", "at", "in", "on", "and", "hall", "theater", "theatre",
    "center", "centre", "arena", "coliseum", "auditorium", "civic",
    "memorial", "municipal", "convention", "amphitheater", "amphitheatre",
    "stadium", "ballroom", "pavilion", "college", "university", "park",
    "field", "house", "building", "complex", "room", "club", "lounge",
    "garden", "gardens", "hotel", "street", "avenue", "road", "for",
    "new", "old", "music", "concert", "music hall",
}

def significant_words(name):
    words = re.findall(r"[a-z0-9]+", name.lower())
    return {w for w in words if len(w) >= 4 and w not in NAME_STOPWORDS}

def plausible_match(venue_name, resolved_title, city=None, extract=None):
    """A sniff test, not proof. Word overlap alone wrongly convicts every
    *genuine* rename — "USAir Arena" -> "Capital Centre", "Rich Stadium" ->
    "Ralph Wilson Stadium" share no words at all, and renames are exactly the
    case this feature is for. So: accept on name overlap, OR on the venue's
    own city turning up in the resolved title or its lead extract (a real
    building's article almost always says where it is, rename or not)."""
    a = significant_words(venue_name)
    b = significant_words(re.sub(r"\s*\([^)]*\)\s*$", "", resolved_title))
    if not a or not b:
        return True   # too short on either side to judge — don't block on it
    if a & b:
        return True
    if city:
        city_name = city.split(",")[0].strip()
        if len(city_name) >= 4:
            haystack = f"{resolved_title} {extract or ''}".lower()
            if city_name.lower() in haystack:
                return True
    return False


def split_fields(box):
    """{{Infobox X | a = 1 | b = {{y|2}} }} -> {'a':'1','b':'{{y|2}}'}, splitting
    only on top-level pipes (inside {{ }} / [[ ]] doesn't count)."""
    inner = re.sub(r"^\{\{\s*Infobox[^\n|]*", "", box, flags=re.I)
    inner = re.sub(r"\}\}\s*$", "", inner)
    parts, buf, depth = [], "", 0
    i = 0
    while i < len(inner):
        two = inner[i:i+2]
        if two in ("{{", "[["):
            depth += 1; buf += two; i += 2; continue
        if two in ("}}", "]]"):
            depth -= 1; buf += two; i += 2; continue
        c = inner[i]
        if c == "|" and depth == 0:
            parts.append(buf); buf = ""; i += 1; continue
        buf += c; i += 1
    parts.append(buf)
    fields = {}
    for p in parts:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip().lower()
        if k:
            fields[k] = v
    return fields


THIS_YEAR = datetime.date.today().year

def first_year(text):
    """A year out of a plausible range is a parsing artifact (a capacity
    figure, an address number, a stray reference year), not a real date —
    drop it rather than show something like "Built 8500" or "Built 2099"."""
    for m in re.finditer(r"\d{4}", text or ""):
        y = int(m.group())
        if 1600 <= y <= THIS_YEAR + 1:
            return y
    return None


def parse_venue_facts(wikitext, title):
    box = find_infobox(wikitext)
    fields = split_fields(box) if box else {}

    def get(*names):
        for n in names:
            if n in fields and clean(fields[n]):
                return clean(fields[n])
        return None

    opened_raw = get("opened", "built", "broke_ground", "groundbreaking", "built_date", "opening_date")
    closed_raw = get("closed", "demolished", "demolition_date", "closed_date")
    years_active = get("yearsactive", "years_active")

    year_built = first_year(opened_raw)
    year_closed = first_year(closed_raw)
    if year_built is None and years_active:
        m = re.match(r"\D*(\d{4})", years_active)
        if m:
            year_built = int(m.group(1))
    demolished = year_closed is not None

    former_raw = get("former_names", "othernames", "other_names", "alternate_names", "building_name", "fullname")
    former_names = []
    if former_raw:
        for chunk in re.split(r";|(?<=\))\s*,|\n", former_raw):
            chunk = chunk.strip(" ,;")
            # Guard against leftover template/link syntax an unusual infobox
            # shape slipped past clean(), and against noise too short to be
            # a real building name.
            if chunk and 2 < len(chunk) <= 90 and not re.search(r"[{}\[\]|=]", chunk) and chunk.lower() != title.lower():
                former_names.append(chunk)

    current_name = None if demolished else re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()

    return {
        "yearBuilt": year_built,
        "demolished": demolished,
        "yearDemolished": year_closed,
        "formerNames": former_names[:6],
        "currentName": current_name,
    }


IMG_BLOCKLIST = re.compile(
    r"icon|logo|flag|symbol|locator|seal|coat.?of.?arms|ambox|padlock|edit-icon|"
    r"wiki|commons-logo|question.?book|folder|nowrap|disambig|nuvola|crystal|"
    r"blank|sound-icon|loudspeaker|pictogram|map\b|\bmap_|_map\b|button|arrow|"
    r"ferry|ogg\.svg|stub|wiktionary|template",
    re.I,
)

def fetch_page(title):
    """One request: infobox wikitext (lead section only) + primary photo + intro."""
    r = api_get({
        "action": "query", "format": "json",
        "prop": "revisions|pageimages|extracts",
        "rvprop": "content", "rvslots": "main", "rvsection": "0",
        "piprop": "thumbnail", "pithumbsize": "1000",
        "exintro": "1", "explaintext": "1", "exsentences": "2",
        "redirects": "1", "titles": title,
    })
    pages = r.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()), None)
    if not page or "missing" in page:
        return None
    wikitext = ""
    revs = page.get("revisions") or []
    if revs:
        wikitext = revs[0].get("slots", {}).get("main", {}).get("*", "") or ""
    thumb = (page.get("thumbnail") or {}).get("source")
    extract = (page.get("extract") or "").strip()
    resolved_title = page.get("title", title)
    return wikitext, thumb, extract, resolved_title


def fetch_gallery(title, primary_src):
    """A handful of real photos off the page, icons and maps filtered out."""
    try:
        r = api_get({
            "action": "query", "format": "json",
            "generator": "images", "gimlimit": "20",
            "prop": "imageinfo", "iiprop": "url|mime|size",
            "iiurlwidth": "900", "redirects": "1", "titles": title,
        })
        pages = r.json().get("query", {}).get("pages", {}) or {}
    except Exception:
        return []
    out = []
    page_url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    for p in pages.values():
        info = (p.get("imageinfo") or [None])[0]
        if not info:
            continue
        src = info.get("thumburl") or info.get("url")
        if not src or src == primary_src:
            continue
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if (info.get("width") or 0) < 400 or (info.get("height") or 0) < 250:
            continue
        if IMG_BLOCKLIST.search(p.get("title", "")):
            continue
        out.append({"src": src, "page": page_url})
        if len(out) >= 5:
            break
    return out


# ─── scrape ──────────────────────────────────────────────────────────────
def unique_venues():
    eras = json.loads(DATA.read_text(encoding="utf-8"))
    shows = [s for e in eras for s in e["shows"]]
    seen = {}
    for s in shows:
        name = venue_name(s["venue"])
        key = venue_key(s["venue"], s["city"])
        year = int(s["date"][:4])
        if key not in seen:
            seen[key] = {"key": key, "name": name, "city": s["city"], "count": 0,
                         "firstYear": year, "lastYear": year}
        v = seen[key]
        v["count"] += 1
        v["firstYear"] = min(v["firstYear"], year)
        v["lastYear"] = max(v["lastYear"], year)
    return sorted(seen.values(), key=lambda v: -v["count"])


def temporally_plausible(facts, first_year, last_year):
    """The strongest check available, and it costs nothing extra: we already
    know exactly which years the band played a given venue, so a resolved
    building that wasn't built yet — or was already demolished — during those
    years is definitely the wrong article, whatever its name or infobox type
    look like. (A year of slack either way for approximate opening/closing
    dates and year-boundary shows.)"""
    if facts.get("yearBuilt") and facts["yearBuilt"] > first_year + 1:
        return False, f"built {facts['yearBuilt']}, after the first show here ({first_year})"
    if facts.get("demolished") and facts.get("yearDemolished") and facts["yearDemolished"] < last_year - 1:
        return False, f"demolished {facts['yearDemolished']}, before the last show here ({last_year})"
    return True, None


def cache_path(key):
    # venueKey is "slug|slug" to match index.html's JS exactly, but '|' isn't
    # legal in a Windows filename — swap it for the cache file's name only;
    # the real key (with the pipe) travels inside the JSON itself.
    return CACHE / (key.replace("|", "--") + ".json")


def scrape():
    CACHE.mkdir(exist_ok=True)
    venues = unique_venues()
    todo = [v for v in venues if not cache_path(v["key"]).exists()]
    print(f"{len(venues)} unique venues, {len(venues) - len(todo)} already cached, "
          f"{len(todo)} to fetch — roughly {len(todo) * 2.3 / 60:.0f} minutes\n")

    ok = empty = fail = 0
    for i, v in enumerate(todo, 1):
        try:
            title = resolve_title(v["name"], v["city"])
            time.sleep(DELAY_SEC)
            if not title:
                cache_path(v["key"]).write_text(json.dumps({"unresolved": True, "key": v["key"]}), encoding="utf-8")
                empty += 1
                print(f"  [{i}/{len(todo)}] {v['name']} ({v['city']})  no Wikipedia match")
                continue

            page = fetch_page(title)
            time.sleep(DELAY_SEC)
            if not page:
                cache_path(v["key"]).write_text(json.dumps({"unresolved": True, "key": v["key"]}), encoding="utf-8")
                empty += 1
                print(f"  [{i}/{len(todo)}] {v['name']}  '{title}' didn't resolve to a page")
                continue
            wikitext, thumb, extract, resolved_title = page

            itype = infobox_type(wikitext)
            if is_non_venue_type(itype):
                cache_path(v["key"]).write_text(
                    json.dumps({"unresolved": True, "key": v["key"], "rejectedTitle": resolved_title, "rejectedType": itype}),
                    encoding="utf-8")
                empty += 1
                print(f"  [{i}/{len(todo)}] {v['name']}  '{resolved_title}' is a {itype} article, not a venue — skipped")
                continue

            if v["name"] not in CURATED_WIKI and not plausible_match(v["name"], resolved_title, v["city"], extract):
                cache_path(v["key"]).write_text(
                    json.dumps({"unresolved": True, "key": v["key"], "rejectedTitle": resolved_title, "rejectedReason": "no name overlap"}),
                    encoding="utf-8")
                empty += 1
                print(f"  [{i}/{len(todo)}] {v['name']}  '{resolved_title}' shares no name with the venue — skipped")
                continue

            facts = parse_venue_facts(wikitext, resolved_title)

            ok_time, why_not = temporally_plausible(facts, v["firstYear"], v["lastYear"])
            if not ok_time:
                cache_path(v["key"]).write_text(
                    json.dumps({"unresolved": True, "key": v["key"], "rejectedTitle": resolved_title, "rejectedReason": why_not}),
                    encoding="utf-8")
                empty += 1
                print(f"  [{i}/{len(todo)}] {v['name']}  '{resolved_title}' {why_not} — skipped")
                continue

            gallery = fetch_gallery(resolved_title, thumb)
            time.sleep(DELAY_SEC)

            photos = ([{"src": thumb, "page": "https://en.wikipedia.org/wiki/" + resolved_title.replace(" ", "_")}]
                      if thumb else []) + gallery

            record = {
                "key": v["key"], "name": v["name"], "city": v["city"], "wikiTitle": resolved_title,
                "infoboxType": itype, "extract": extract or None, "photos": photos, **facts,
            }
            cache_path(v["key"]).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            ok += 1
            flag = []
            if facts["yearBuilt"]: flag.append(f"built {facts['yearBuilt']}")
            if facts["demolished"]: flag.append(f"demolished {facts['yearDemolished']}")
            if facts["formerNames"]: flag.append(f"{len(facts['formerNames'])} former name(s)")
            flag.append(f"{len(photos)} photo(s)")
            print(f"  [{i}/{len(todo)}] {v['name']}  →  {resolved_title}  ({', '.join(flag) or 'no facts found'})")
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(todo)}] {v['name']}  FAILED: {e}")
            time.sleep(DELAY_SEC)

    print(f"\nDone. {ok} resolved, {empty} with no match, {fail} failed. Run with --merge to apply.")


def retry_failed():
    n = 0
    for f in CACHE.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("unresolved"):
            f.unlink()
            n += 1
    print(f"Cleared {n} unresolved cache entries — run again to retry them.")


def audit():
    """Re-checks already-cached *successful* entries against the current
    NON_VENUE_INFOBOX / plausible_match rules, offline (using the infoboxType,
    name and wikiTitle already saved in each record — no re-fetching). Lets a
    rule tightened after a scrape already ran catch up without re-downloading
    everything: a plain `python scrape_venues.py` afterward only re-resolves
    whatever this flags."""
    years = {v["key"]: v for v in unique_venues()}
    n = 0
    for f in sorted(CACHE.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("unresolved"):
            continue
        bad_type = is_non_venue_type(data.get("infoboxType"))
        bad_overlap = (data["name"] not in CURATED_WIKI
                       and not plausible_match(data["name"], data["wikiTitle"], data.get("city"), data.get("extract")))
        yv = years.get(data.get("key"))
        ok_time, why_not = temporally_plausible(data, yv["firstYear"], yv["lastYear"]) if yv else (True, None)
        if bad_type or bad_overlap or not ok_time:
            why = (f"infobox type '{data.get('infoboxType')}'" if bad_type
                   else why_not if not ok_time else "no name or city match")
            print(f"  flagging {data['name']} -> {data['wikiTitle']}  ({why})")
            f.unlink()
            n += 1
    print(f"\nCleared {n} entries that fail the current rules — run the scrape again to re-resolve them.")


# ─── merge ───────────────────────────────────────────────────────────────
def merge():
    if not CACHE.exists():
        print("Nothing cached yet — run the scrape first.")
        return
    out = {}
    resolved = 0
    for f in sorted(CACHE.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("unresolved"):
            continue
        key = data.get("key") or f.stem.replace("--", "|")
        out[key] = data
        resolved += 1
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT.name}: {resolved} venues with data.")


if __name__ == "__main__":
    if "--merge" in sys.argv:
        merge()
    elif "--retry-failed" in sys.argv:
        retry_failed()
    elif "--audit" in sys.argv:
        audit()
    else:
        scrape()
