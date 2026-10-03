"""
scrape_venues.py (Phish) — venue history + photos from Wikipedia.

A thin wrapper over the Dead app's ../scrape_venues.py: same resolver, same
plausibility checks, same output shape. Only the inputs differ — this
folder's shows-data.json, its own cache, and a curated map of Phish rooms.

Usage (from this folder, after scrape_phish.py):
    pip install requests
    python scrape_venues.py          # resumable
    python scrape_venues.py --merge  # writes venues-data.json
"""

import importlib.util, sys
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("dead_venues", HERE.parent / "scrape_venues.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

base.ROOT  = HERE
base.DATA  = HERE / "shows-data.json"
base.CACHE = HERE / ".venue-cache"
base.OUT   = HERE / "venues-data.json"
base.session.headers["User-Agent"] = "PhishTimeMachine/1.0 (personal project; respectful, ~1 req/sec)"

# Keep in sync with VENUE_WIKI in index.html. Names are phish.net's spellings.
base.VENUE_RENAME = {}
base.CURATED_WIKI = {
    "Madison Square Garden": "Madison Square Garden",
    "Hampton Coliseum": "Hampton Coliseum",
    "Alpine Valley Music Theatre": "Alpine Valley Music Theatre",
    "Deer Creek Music Center": "Deer Creek Music Center",
    "Red Rocks Amphitheatre": "Red Rocks Amphitheatre",
    "The Gorge": "Gorge Amphitheatre",
    "The Gorge Amphitheatre": "Gorge Amphitheatre",
    "Dick's Sporting Goods Park": "Dick's Sporting Goods Park",
    "Saratoga Performing Arts Center": "Saratoga Performing Arts Center",
    "Merriweather Post Pavilion": "Merriweather Post Pavilion",
    "Great Woods Center for the Performing Arts": "Xfinity Center (Mansfield, Massachusetts)",
    "Shoreline Amphitheatre": "Shoreline Amphitheatre",
    "Nassau Veterans Memorial Coliseum": "Nassau Veterans Memorial Coliseum",
    "Worcester Centrum": "DCU Center",
    "The Centrum": "DCU Center",
    "Boston Garden": "Boston Garden",
    "Fox Theatre": "Fox Theatre (Atlanta)",
    "Bill Graham Civic Auditorium": "Bill Graham Civic Auditorium",
    "Hollywood Bowl": "Hollywood Bowl",
    "Fenway Park": "Fenway Park",
    "Wrigley Field": "Wrigley Field",
    "Nectar's": "Nectar's",
    "Higher Ground": "Higher Ground (music venue)",
}

if __name__ == "__main__":
    if "--merge" in sys.argv:
        base.merge()
    elif "--retry-failed" in sys.argv:
        base.retry_failed()
    elif "--audit" in sys.argv:
        base.audit()
    else:
        base.scrape()
