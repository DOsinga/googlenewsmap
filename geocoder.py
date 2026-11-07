"""Resolve a place name to coordinates.

Two-stage lookup:
  1. Local GeoNames gazetteer (cities5000 + countries), free, in-process.
  2. OSM Nominatim, network call, rate-limited to 1 req / sec.

All results land in the news.db `locations` table — including negatives
(`Nvidia → not_found`) so we never re-query the same string twice.
"""
import csv
import functools
import os
import time

import requests

from . import store

HERE = os.path.dirname(__file__)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "douwe.com/googlenewsmap (https://douwe.com)"
NOMINATIM_MIN_INTERVAL = 1.1  # seconds between requests (their policy: ≤1/sec)

# Nominatim hits below this importance score are rejected. Cuts out
# tiny towns that happen to share company names ("Cisco, TX" = 0.47)
# while keeping legit small but newsworthy places (Davos = 0.62).
NOMINATIM_MIN_IMPORTANCE = 0.5

# Geographic centroids for the 50 US states + DC. Added to the gazetteer with
# a synthetic high population so e.g. "California" wins over the town of
# California, Maryland (pop 12k). Pop is set below countries (10^10) so
# "Georgia" still resolves to the country, not the state.
US_STATES = {
    "Alabama": (32.806671, -86.791130),
    "Alaska": (61.370716, -152.404419),
    "Arizona": (33.729759, -111.431221),
    "Arkansas": (34.969704, -92.373123),
    "California": (36.116203, -119.681564),
    "Colorado": (39.059811, -105.311104),
    "Connecticut": (41.597782, -72.755371),
    "Delaware": (39.318523, -75.507141),
    "Florida": (27.766279, -81.686783),
    "Georgia": (33.040619, -83.643074),
    "Hawaii": (21.094318, -157.498337),
    "Idaho": (44.240459, -114.478828),
    "Illinois": (40.349457, -88.986137),
    "Indiana": (39.849426, -86.258278),
    "Iowa": (42.011539, -93.210526),
    "Kansas": (38.526600, -96.726486),
    "Kentucky": (37.668140, -84.670067),
    "Louisiana": (31.169546, -91.867805),
    "Maine": (44.693947, -69.381927),
    "Maryland": (39.063946, -76.802101),
    "Massachusetts": (42.230171, -71.530106),
    "Michigan": (43.326618, -84.536095),
    "Minnesota": (45.694454, -93.900192),
    "Mississippi": (32.741646, -89.678696),
    "Missouri": (38.456085, -92.288368),
    "Montana": (46.921925, -110.454353),
    "Nebraska": (41.125370, -98.268082),
    "Nevada": (38.313515, -117.055374),
    "New Hampshire": (43.452492, -71.563896),
    "New Jersey": (40.298904, -74.521011),
    "New Mexico": (34.840515, -106.248482),
    "North Carolina": (35.630066, -79.806419),
    "North Dakota": (47.528912, -99.784012),
    "Ohio": (40.388783, -82.764915),
    "Oklahoma": (35.565342, -96.928917),
    "Oregon": (44.572021, -122.070938),
    "Pennsylvania": (40.590752, -77.209755),
    "Rhode Island": (41.680893, -71.511780),
    "South Carolina": (33.856892, -80.945007),
    "South Dakota": (44.299782, -99.438828),
    "Tennessee": (35.747845, -86.692345),
    "Texas": (31.054487, -97.563461),
    "Utah": (40.150032, -111.862434),
    "Vermont": (44.045876, -72.710686),
    "Virginia": (37.769337, -78.169968),
    "West Virginia": (38.491226, -80.954453),
    "Wisconsin": (44.268543, -89.616508),
    "Wyoming": (42.755966, -107.302490),
    # "Washington" and "New York" intentionally omitted: in news headlines
    # they almost always mean DC / NYC, which the cities gazetteer covers.
}


@functools.cache
def _gazetteer() -> dict[str, dict]:
    """name (lower) -> {lat, lon, display_name, country_code, pop, source}."""
    table: dict[str, dict] = {}

    def add(name: str, lat: float, lon: float, display: str, cc: str, pop: int):
        key = store.normalize(name)
        if len(key) < 3:
            return
        existing = table.get(key)
        if existing is None or pop > existing["pop"]:
            table[key] = {"lat": lat, "lon": lon, "display_name": display,
                          "country_code": cc, "pop": pop, "source": "gazetteer"}

    with open(os.path.join(HERE, "cities5000.txt"), encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) < 15:
                continue
            try:
                lat, lon = float(row[4]), float(row[5])
                pop = int(row[14] or 0)
            except ValueError:
                continue
            cc = row[8]
            display = f"{row[1]}, {cc}" if cc else row[1]
            for name in (row[1], row[2], *row[3].split(",")):
                if name:
                    add(name, lat, lon, display, cc, pop)

    # Countries: pin at their capital city, tagged as country win-over-cities by giving
    # them a synthetic very-high population so "Iran" wins over "Iran, USA"-style noise.
    with open(os.path.join(HERE, "countryInfo.txt"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            iso, _iso3, _num, _fips, country, capital = parts[:6]
            if capital:
                cap_key = store.normalize(capital)
                cap = table.get(cap_key)
                if cap:
                    for name in (country, parts[1]):
                        add(name, cap["lat"], cap["lon"], country, iso, 10**10)

    # US states: pop high enough to beat same-named cities globally
    # (Floridablanca 268k, Virginia ZA 123k, Oklahoma City alt 681k…),
    # but still under countries (10^10) so "Georgia" stays the country.
    # Washington & New York are *not* in this list — see US_STATES.
    for state, (lat, lon) in US_STATES.items():
        add(state, lat, lon, f"{state}, US", "US", 10_000_000)

    return table


_last_nominatim_call = 0.0


def _short_display(query: str, top: dict, address: dict) -> str:
    """'Place, Country' — never the kitchen-sink Nominatim display_name."""
    country = address.get("country") or ""
    # For office/company hits Nominatim sets top['name'] to the company
    # ('Adobe'); for cities/states address.city/state has the name. The
    # query string is the last fallback.
    local = (
        top.get("name")
        or address.get("city") or address.get("town") or address.get("village")
        or address.get("state") or address.get("region") or address.get("county")
        or query
    )
    if country and local and local != country:
        return f"{local}, {country}"
    return local or country or query


def _nominatim(name: str):
    """Single Nominatim query, respecting their 1 req/sec rate limit."""
    global _last_nominatim_call
    wait = NOMINATIM_MIN_INTERVAL - (time.time() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={
                "q": name, "format": "json", "limit": 1,
                "accept-language": "en", "addressdetails": 1,
            },
            headers={"User-Agent": NOMINATIM_UA},
            timeout=15,
        )
        _last_nominatim_call = time.time()
        if r.status_code != 200:
            return None
        results = r.json()
        if not results:
            return None
        top = results[0]
        try:
            importance = float(top.get("importance") or 0)
        except (TypeError, ValueError):
            importance = 0
        if importance < NOMINATIM_MIN_IMPORTANCE:
            return None
        address = top.get("address") or {}
        return {
            "lat": float(top["lat"]),
            "lon": float(top["lon"]),
            "display_name": _short_display(name, top, address),
            "country_code": (address.get("country_code") or "").upper(),
            "source": "nominatim",
        }
    except (requests.RequestException, ValueError):
        _last_nominatim_call = time.time()
        return None


def resolve(conn, name: str) -> dict | None:
    """Return {lat, lon, display_name, country_code} for `name`, or None.

    Reads/writes the locations table.
    """
    key = store.normalize(name)
    if not key or len(key) < 3:
        return None

    cached = store.get_location(conn, key)
    if cached is not None:
        if cached["not_found"]:
            return None
        return {"lat": cached["lat"], "lon": cached["lon"],
                "display_name": cached["display_name"],
                "country_code": cached["country_code"]}

    hit = _gazetteer().get(key)
    if hit:
        store.upsert_location(
            conn, name=key, lat=hit["lat"], lon=hit["lon"],
            display_name=hit["display_name"], country_code=hit["country_code"],
            source="gazetteer",
        )
        conn.commit()
        return hit

    hit = _nominatim(name)
    if hit:
        store.upsert_location(
            conn, name=key, lat=hit["lat"], lon=hit["lon"],
            display_name=hit["display_name"], country_code=hit["country_code"],
            source="nominatim",
        )
        conn.commit()
        return hit

    # negative cache so we never re-query "Nvidia"
    store.upsert_location(
        conn, name=key, lat=None, lon=None, display_name=None,
        country_code=None, source="nominatim", not_found=True,
    )
    conn.commit()
    return None
