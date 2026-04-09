"""
Dead Time Machine — setlists.net scraper
Run this locally to build the full show database.

Usage:
    pip install requests beautifulsoup4
    python scrape_setlists.py

Output:
    shows.json   — all shows, structured for the web app
    errors.txt   — any IDs that failed or returned no show

How it works:
    setlists.net uses sequential ?show_id= integers (1 → ~2400).
    We try every ID, parse the setlist, skip blanks.
    Rate-limited to ~1 req/sec to be polite.

After running, replace the ERAS / ALL_SHOWS data block in
index.html with the generated shows.json content using the
helper at the bottom of this file.
"""

import json, re, time, sys
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE_URL   = "https://www.setlists.net/"
MAX_ID     = 2450    # a few past the last known show (2348)
DELAY_SEC  = 1.1     # seconds between requests — be kind
OUT_FILE   = Path("shows.json")
ERR_FILE   = Path("errors.txt")

MONTHS = {
    "01":"January","02":"February","03":"March","04":"April",
    "05":"May","06":"June","07":"July","08":"August",
    "09":"September","10":"October","11":"November","12":"December"
}
DOW = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

ERA_MAP = [
    ((1965,1972), "Pigpen & The Acid Years",    "1965–1972", "#8B4513"),
    ((1972,1979), "The Keith & Donna Years",     "1972–1979", "#1A6B55"),
    ((1979,1990), "The Brent Years",             "1979–1990", "#185FA5"),
    ((1990,1995), "The Final Years",             "1990–1995", "#8B2FC9"),
]

def get_era(year):
    for (start, end), name, years, color in ERA_MAP:
        if start <= year <= end:
            return {"name": name, "years": years, "color": color}
    return {"name": "Unknown", "years": "", "color": "#555"}

def parse_show_page(html, show_id):
    """Parse a setlists.net show page. Returns dict or None."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")

    # Find "1 Show Found"
    if "1 Show Found" not in text:
        return None

    # Extract date and venue line — appears after "1 Show Found"
    # Format: MM/DD/YY\n\nVENUE - CITY, STATE
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    try:
        found_idx = next(i for i,l in enumerate(lines) if "1 Show Found" in l)
    except StopIteration:
        return None

    # Find date line (MM/DD/YY format)
    date_str = None
    venue_city = None
    for line in lines[found_idx:found_idx+20]:
        m = re.match(r'^(\d{2})/(\d{2})/(\d{2})$', line)
        if m:
            mo, day, yr = m.groups()
            year = int("19" + yr)
            date_str = f"{year}-{mo}-{day}"
            continue
        if date_str and not venue_city and " - " in line and len(line) > 5:
            venue_city = line
            break

    if not date_str or not venue_city:
        return None

    # Parse venue / city
    parts = venue_city.split(" - ", 1)
    venue = parts[0].strip()
    city  = parts[1].strip() if len(parts) > 1 else ""

    # Day of week from date
    import datetime
    try:
        dt = datetime.date(int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10]))
        dow = DOW[dt.weekday()]
    except:
        dow = ""

    # Parse sets
    sets = []
    set_labels = ["Set 1:", "Set 2:", "Set 3:", "Set 4:", "Encore:"]

    for label in set_labels:
        if label not in text:
            continue
        # Extract songs between this label and the next
        start_idx = text.index(label) + len(label)
        # Find next label or "Download/Listen"
        end_idx = len(text)
        for other in set_labels + ["Download/Listen"]:
            if other != label and other in text[start_idx:]:
                candidate = text.index(other, start_idx)
                if candidate < end_idx:
                    end_idx = candidate

        songs_raw = text[start_idx:end_idx]
        songs = [s.strip() for s in songs_raw.strip().split("\n") if s.strip() and s.strip() not in ["", "  "]]
        # Filter junk lines
        songs = [s for s in songs if len(s) > 1 and not s.startswith("http") and not s.startswith("[")]

        if songs:
            is_enc = label == "Encore:"
            # Normalize set label
            set_num = label.replace(":", "")
            if set_num == "Set 1": set_num = "Set One"
            elif set_num == "Set 2": set_num = "Set Two"
            elif set_num == "Set 3": set_num = "Set Three"
            elif set_num == "Set 4": set_num = "Set Four"
            sets.append({"lbl": set_num, "songs": songs, "enc": is_enc})

    if not sets:
        return None

    year = int(date_str[:4])
    return {
        "id":    str(show_id).zfill(4),
        "date":  date_str,
        "dow":   dow,
        "venue": venue,
        "city":  city,
        "badge": None,
        "sets":  sets,
        "era":   get_era(year)
    }

def scrape_all():
    shows  = []
    errors = []
    session = requests.Session()
    session.headers["User-Agent"] = "DeadTimeMachine/1.0 (personal project; respectful scraper)"

    print(f"Scraping setlists.net — IDs 1 to {MAX_ID}")
    print(f"Estimated time: ~{MAX_ID * DELAY_SEC / 60:.0f} minutes\n")

    for show_id in range(1, MAX_ID + 1):
        url = f"{BASE_URL}?show_id={show_id:04d}"
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            show = parse_show_page(r.text, show_id)
            if show:
                shows.append(show)
                print(f"  ✓ {show['date']}  {show['venue'][:50]}")
            else:
                # Empty / no show at this ID — normal
                sys.stdout.write(f"\r  - {show_id}/{MAX_ID} (no show)")
                sys.stdout.flush()
        except Exception as e:
            errors.append(f"{show_id}: {e}")
            print(f"  ✗ {show_id}: {e}")

        time.sleep(DELAY_SEC)

    print(f"\n\nDone. {len(shows)} shows scraped, {len(errors)} errors.")

    OUT_FILE.write_text(json.dumps(shows, indent=2, ensure_ascii=False))
    print(f"→ {OUT_FILE} written")

    if errors:
        ERR_FILE.write_text("\n".join(errors))
        print(f"→ {ERR_FILE} written")

    return shows

def inject_into_html(shows, html_path="index.html"):
    """
    Replace the ERAS / ALL constant in index.html with scraped data.
    Run after scrape_all() completes.
    """
    # Group by era for the ERAS array structure
    from collections import defaultdict
    era_buckets = defaultdict(list)
    for s in shows:
        key = s["era"]["name"]
        era_buckets[key].append(s)

    era_order = [e[1] for e in ERA_MAP]
    eras_js = []
    for era_name in era_order:
        shows_in_era = era_buckets.get(era_name, [])
        if not shows_in_era:
            continue
        era_meta = next((e for e in ERA_MAP if e[1] == era_name), None)
        era_js = {
            "name": era_name,
            "years": era_meta[2] if era_meta else "",
            "color": era_meta[3] if era_meta else "#555",
            "shows": shows_in_era
        }
        eras_js.append(era_js)

    js_blob = "const ERAS = " + json.dumps(eras_js, indent=2, ensure_ascii=False) + ";"

    html = Path(html_path).read_text()
    # Find and replace the ERAS block
    start_marker = "const ERAS = ["
    end_marker   = "];\n\nconst ALL"
    if start_marker in html and end_marker in html:
        start = html.index(start_marker)
        end   = html.index(end_marker) + 2  # include "];
        html  = html[:start] + js_blob + html[end:]
        Path(html_path).write_text(html)
        print(f"→ {html_path} updated with {len(shows)} shows")
    else:
        print("Could not find injection markers in HTML — paste manually.")

if __name__ == "__main__":
    shows = scrape_all()
    # Optionally inject into HTML
    if Path("index.html").exists():
        inject_into_html(shows)
