"""
scrape_segues.py — cross-reference jerrygarcia.com for segue notation.

setlists.net, the source behind shows-data.json, does not publish segues at
all: Cornell's set two comes across as "Scarlet Begonias, Fire On The
Mountain", not "Scarlet > Fire". jerrygarcia.com's show pages do carry them,
marked inline in the setlist list items:

    <li><a href="/song/scarlet-begonias/">Scarlet Begonias</a> > </li>
    <li><a href="/song/fire-on-the-mountain/">Fire On The Mountain</a></li>

(DeadBase was the other candidate. deadbase.com no longer resolves.)

Usage:
    pip install requests beautifulsoup4
    python scrape_segues.py          # resumable — safe to stop and re-run
    python scrape_segues.py --merge  # fold the cache into shows-data.json

Output:
    .segue-cache/<date>.json   one file per show, so a re-run costs nothing
    shows-data.json            updated in place by --merge (writes a .bak first)
"""

import json, re, sys, time, difflib
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT      = Path(__file__).parent
DATA      = ROOT / "shows-data.json"
CACHE     = ROOT / ".segue-cache"
URL_CACHE = CACHE / "_urls.txt"
DELAY_SEC = 1.0
UA        = "DeadTimeMachine/1.0 (personal project; respectful, 1 req/sec)"

session = requests.Session()
session.headers["User-Agent"] = UA


# ─── show URL index ──────────────────────────────────────────────────────
def show_urls():
    """Every /show/ URL on jerrygarcia.com, from its sitemaps."""
    if URL_CACHE.exists():
        return URL_CACHE.read_text(encoding="utf-8").split()
    urls = []
    for n in ("", "2", "3", "4"):
        r = session.get(f"https://jerrygarcia.com/show-sitemap{n}.xml", timeout=30)
        r.raise_for_status()
        urls += re.findall(r"<loc>(https://jerrygarcia\.com/show/[^<]+)</loc>", r.text)
        time.sleep(DELAY_SEC)
    CACHE.mkdir(exist_ok=True)
    URL_CACHE.write_text("\n".join(urls), encoding="utf-8")
    return urls


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def resolve(shows, urls):
    """date -> URL. Several dates have two shows; pick the closer venue slug."""
    by_date = {}
    for u in urls:
        m = re.search(r"/show/(\d{4}-\d{2}-\d{2})-(.+?)/$", u)
        if m:
            by_date.setdefault(m.group(1), []).append((m.group(2), u))

    out, ambiguous = {}, 0
    for s in shows:
        cands = by_date.get(s["date"])
        if not cands:
            continue
        if len(cands) == 1:
            out[s["date"]] = cands[0][1]
            continue
        ambiguous += 1
        want = slug(s["venue"] + s["city"])
        best = max(cands, key=lambda c: difflib.SequenceMatcher(None, want, slug(c[0])).ratio())
        out[s["date"]] = best[1]
    print(f"  resolved {len(out)}/{len(shows)} dates ({ambiguous} needed venue matching)")
    return out


# ─── parsing ─────────────────────────────────────────────────────────────
def parse_sets(html):
    """[{lbl, songs:[{t, seg}]}] — seg means 'segues into the next song'."""
    soup = BeautifulSoup(html, "html.parser")
    sets = []
    for ol in soup.select("ol.comp-set-list"):
        title = ol.find_previous(class_="set-title")
        songs = []
        for li in ol.find_all("li", recursive=False):
            # Most songs link to a song page, but not all — an unlinked entry
            # reads "<li>Not Fade Away > </li>", so the arrow has to be taken
            # off the whole item's text, not off whatever follows the link.
            raw = li.get_text(" ", strip=True)
            seg = raw.rstrip().endswith(">")
            a = li.find("a")
            name = re.sub(r"\s*>\s*$", "", (a.get_text() if a else raw)).strip()
            if not name:
                continue
            songs.append({"t": name, "seg": seg})
        if songs:
            sets.append({"lbl": title.get_text().strip() if title else "Set", "songs": songs})
    return sets


# ─── scrape ──────────────────────────────────────────────────────────────
def scrape():
    eras = json.loads(DATA.read_text(encoding="utf-8"))
    shows = [s for e in eras for s in e["shows"]]
    print(f"Cross-referencing {len(shows)} shows against jerrygarcia.com")

    CACHE.mkdir(exist_ok=True)
    urls = show_urls()
    print(f"  {len(urls)} show URLs in sitemaps")
    resolved = resolve(shows, urls)

    todo = [s for s in shows if s["date"] in resolved and not (CACHE / f"{s['date']}.json").exists()]
    print(f"  {len(shows) - len(todo) - (len(shows) - len(resolved))} already cached, "
          f"{len(todo)} to fetch — about {len(todo) * DELAY_SEC / 60:.0f} minutes\n")

    ok = fail = 0
    for i, s in enumerate(todo, 1):
        url = resolved[s["date"]]
        try:
            r = session.get(url, timeout=25)
            r.raise_for_status()
            sets = parse_sets(r.text)
            segs = sum(x["seg"] for st in sets for x in st["songs"])
            (CACHE / f"{s['date']}.json").write_text(
                json.dumps({"date": s["date"], "url": url, "sets": sets}, ensure_ascii=False),
                encoding="utf-8")
            ok += 1
            print(f"  [{i}/{len(todo)}] {s['date']}  {len(sets)} sets, {segs} segues")
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(todo)}] {s['date']}  FAILED: {e}")
        time.sleep(DELAY_SEC)

    print(f"\nDone. {ok} fetched, {fail} failed. Run with --merge to apply.")


# ─── merge ───────────────────────────────────────────────────────────────
# Titles differ between the two sources ("St. Stephen" vs "Saint Stephen",
# "Dancin' In The Streets" vs "...Street"), and so does set membership. So we
# align the two song sequences and only carry a segue across where a pair
# genuinely matches — never by position alone.
def norm(t):
    t = t.lower()
    t = re.sub(r"\bsaint\b", "st", t)
    t = re.sub(r"\band\b", "&", t)
    t = re.sub(r"[^a-z0-9&]+", "", t)
    return t


SIM_FLOOR = 0.82   # "Lazy Lightnin'" vs "Lazy Lightning" is .96; unrelated songs sit well below


def align(ours, theirs):
    """Monotonic best alignment of two title sequences -> [(i, j)] pairs.

    A plain difflib match only pairs identical strings, which loses every
    spelling variant between the two sources, and drops segues that fall on a
    block boundary. This is a small Needleman-Wunsch over similarity instead,
    so near-matches pair up and gaps on either side are tolerated.
    """
    n, m = len(ours), len(theirs)
    sim = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(m):
            r = difflib.SequenceMatcher(None, ours[i], theirs[j]).ratio()
            sim[i][j] = r if r >= SIM_FLOOR else 0.0

    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = max(sim[i][j] + best[i + 1][j + 1], best[i + 1][j], best[i][j + 1])

    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if best[i][j] == sim[i][j] + best[i + 1][j + 1] and sim[i][j] > 0:
            pairs.append((i, j)); i += 1; j += 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return pairs


def merge():
    eras = json.loads(DATA.read_text(encoding="utf-8"))
    shows = [s for e in eras for s in e["shows"]]
    by_date = {s["date"]: s for s in shows}

    applied = songs_marked = no_cache = 0
    for f in sorted(CACHE.glob("*.json")):
        if f.name.startswith("_"):
            continue
        jg = json.loads(f.read_text(encoding="utf-8"))
        ours = by_date.get(jg["date"])
        if not ours:
            continue

        their = [x for st in jg["sets"] for x in st["songs"]]
        their_n = [norm(x["t"]) for x in their]

        # Flatten our sets too, keeping a way back to the original slot, so a
        # song that sits in a different set on each site still lines up.
        flat, ref = [], []
        for si, st in enumerate(ours["sets"]):
            for ti, t in enumerate(st["songs"]):
                flat.append(norm(t)); ref.append((si, ti))

        pairs = align(flat, their_n)
        matched = {i: j for i, j in pairs}

        marked = 0
        for i, j in pairs:
            # Only carry the segue if the song it runs into also lines up —
            # otherwise we'd be asserting a transition neither source shows.
            if not their[j]["seg"] or matched.get(i + 1) != j + 1:
                continue
            si, ti = ref[i]
            t = ours["sets"][si]["songs"][ti]
            if not t.rstrip().endswith(">"):
                ours["sets"][si]["songs"][ti] = t.rstrip() + " >"
                marked += 1
        if marked:
            applied += 1
            songs_marked += marked

    if not applied:
        print("Nothing to merge — run the scrape first.")
        return

    bak = DATA.with_suffix(".json.bak")
    bak.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    DATA.write_text(json.dumps(eras, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Merged segues into {applied} shows ({songs_marked} songs marked).")
    print(f"Backup written to {bak.name}")


if __name__ == "__main__":
    if "--merge" in sys.argv:
        merge()
    else:
        scrape()
