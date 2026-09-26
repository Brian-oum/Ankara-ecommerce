"""
Delivery fee lookup + VAT, for checkout.

The business prices shipping off a flat hand-priced chart of named
roads/areas (the framed chart on the shop wall), grouped under a
handful of broader "zone" headings for readability. Each area also has
an approximate lat/lng below, so a customer's map pin can be matched to
the *nearest* priced area (great-circle distance) instead of asking
them to know which named area they're in.

Nothing here trusts the browser: the checkout form still submits an
area *name*, and views.checkout() looks the fee up server-side from
that name via fee_for_area(). The map/nearest-match logic in
checkout.html's JS is just a friendlier way to arrive at that name -
nearest_area() below exists so the same matching can be done/verified
server-side too, if you ever want to.

IMPORTANT - two things need proofreading before this goes live:
1. Area names/prices were transcribed from a photo of the price chart -
   a few were ambiguous and are flagged inline; confirm against the
   original image.
2. Coordinates are approximate placeholders (my best estimate of each
   area's general location in Nairobi), NOT surveyed - nudge them to
   the actual spot (e.g. by right-clicking the location in Google Maps
   or OSM and copying the lat/lng) before relying on nearest-match
   pricing for real orders.
"""

import math
from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP

VAT_RATE = Decimal("0.16")

# name, fee (KES), lat, lng
ZONES = OrderedDict([
    ("Mombasa Road", [
        ("Nyayo Stadium", 200, -1.3049, 36.8226),
        ("Bunyala Rd", 200, -1.3057, 36.8280),
        ("Lusaka Rd", 200, -1.3121, 36.8324),
        ("Ind. Area", 300, -1.3151, 36.8508),
        ("DT Dobi", 300, -1.3200, 36.8560),
        ("Lunga Lunga", 300, -1.3236, 36.8590),
        ("South B & C", 300, -1.3175, 36.8280),
        ("Capital Centre", 300, -1.3183, 36.8390),
        ("OM/Marasmoine", 300, -1.3250, 36.8600),  # ambiguous name in photo - confirm
        ("Cabanas", 400, -1.3350, 36.8850),
        ("SGR", 400, -1.3630, 36.9280),
        ("Mlolongo", 500, -1.3900, 36.9330),
        ("Kitengela", 700, -1.4756, 36.9585),
        ("Athi River", 700, -1.4570, 36.9770),
    ]),
    ("CBD Area", [
        ("Kamukunji", 200, -1.2833, 36.8330),
        ("Gikomba", 200, -1.2820, 36.8330),
        ("Kariokor", 200, -1.2790, 36.8330),
        ("Ngara", 200, -1.2740, 36.8270),
        ("State House", 200, -1.2880, 36.8090),
        ("Within CBD", 200, -1.2833, 36.8172),
    ]),
    ("Thika Rd", [
        ("Muthaiga", 200, -1.2500, 36.8270),
        ("NYS", 200, -1.2460, 36.8380),
        ("Survey", 200, -1.2390, 36.8460),
        ("Allsops", 300, -1.2650, 36.8420),
        ("Garden City", 300, -1.2210, 36.8770),
        ("TRM", 300, -1.2190, 36.8880),
        ("Zimmerman", 350, -1.2100, 36.8960),
        ("Kasarani", 350, -1.2230, 36.8990),
        ("Baba Dogo", 350, -1.2460, 36.8650),
        ("Githurai 44/45", 450, -1.1930, 36.9110),
        ("Kiwanja Ndani", 450, -1.1850, 36.9250),  # ambiguous ("K/Wendani") - confirm
        ("Kimbo/Sukari", 450, -1.1700, 36.9500),   # ambiguous ("K/Sukari") - confirm
        ("Ruiru", 500, -1.1460, 36.9630),
        ("Juja", 500, -1.1030, 37.0130),
        ("Thika", 500, -1.0333, 37.0693),
    ]),
    ("Kiambu Rd", [
        ("Lunar Park", 200, -1.2270, 36.8330),
        ("CID HQRS", 300, -1.2600, 36.8130),
        ("Ridgeways", 300, -1.2050, 36.8280),
        ("Four Ways", 300, -1.2100, 36.8250),
        ("Thindigua", 400, -1.1900, 36.8280),
        ("Kiambu", 400, -1.1714, 36.8356),
    ]),
    ("Ngong Rd", [
        ("KNH", 200, -1.3010, 36.8070),
        ("Hurlingham", 200, -1.2960, 36.7920),
        ("Coptic Hosp", 200, -1.2980, 36.7950),
        ("Prestige", 300, -1.3010, 36.7870),
        ("Adams Arcade", 300, -1.3010, 36.7800),
        ("Junction", 300, -1.3040, 36.7680),
        ("Lenana", 400, -1.3080, 36.7650),
        ("Karen", 450, -1.3190, 36.7080),
        ("Bulbul", 600, -1.3800, 36.6700),
        ("Ngong", 600, -1.3630, 36.6580),
    ]),
    ("Westlands", [
        ("Ojijo", 200, -1.2650, 36.8100),
        ("Westlands", 200, -1.2670, 36.8060),
        ("Westgate", 250, -1.2570, 36.8020),
        ("Sarit Centre", 250, -1.2600, 36.8030),
        ("Kitisuru", 350, -1.2320, 36.7930),
        ("Loresho", 350, -1.2540, 36.7690),
        ("U/Kabete", 350, -1.2380, 36.7500),
    ]),
    ("Gatanga Rd", [
        ("Yaya Centre", 250, -1.2920, 36.7870),
        ("Kileleshwa", 300, -1.2800, 36.7800),
        ("Kilimani", 300, -1.2910, 36.7830),
        ("Lavington", 300, -1.2780, 36.7680),
        ("Amboseli", 400, -1.2850, 36.7750),  # ambiguous name in photo - confirm
        ("Valley Arcade", 400, -1.2760, 36.7730),
        ("Kawangware", 400, -1.2850, 36.7480),
    ]),
    ("Limuru Rd", [
        ("Fig Tree", 200, -1.2400, 36.8050),
        ("Soma Plaza", 200, -1.2450, 36.8080),  # ambiguous name in photo - confirm
        ("City Park", 200, -1.2600, 36.8130),
        ("Karura", 300, -1.2350, 36.8200),
        ("Village Mkt", 300, -1.2280, 36.8030),
        ("Two Rivers", 400, -1.2150, 36.7900),
        ("Ruaka", 400, -1.2020, 36.7810),
        ("Banana", 500, -1.2200, 36.7900),
        ("Runda", 500, -1.2140, 36.8110),
    ]),
    ("Eastlands", [
        ("Pangani", 200, -1.2700, 36.8330),
        ("Eastleigh", 200, -1.2740, 36.8460),
        ("Huruma", 300, -1.2620, 36.8560),
        ("Kariobangi", 350, -1.2560, 36.8730),
        ("Dandora", 450, -1.2500, 36.8930),
    ]),
    ("Langata Rd", [
        ("Mbagathi", 200, -1.3080, 36.8020),
        ("K-Market", 200, -1.3120, 36.7950),
        ("Madaraka", 300, -1.3110, 36.8210),
        ("T-Mall", 300, -1.3280, 36.7920),
        ("Wilson", 300, -1.3220, 36.8150),
        ("Carnivore", 300, -1.3280, 36.7870),
        ("Bomas/Galleria", 400, -1.3400, 36.7620),  # ambiguous name in photo - confirm
        ("Rongai", 600, -1.3970, 36.7530),
        ("The Hub", 300, -1.3300, 36.7620),
    ]),
    ("Jogoo Rd", [
        ("City Stadium", 200, -1.2970, 36.8390),
        ("Shauri Moyo", 200, -1.2870, 36.8380),
        ("Bahati", 300, -1.2900, 36.8460),
        ("Buruburu", 300, -1.2860, 36.8720),
        ("Donholm", 350, -1.2920, 36.8880),
        ("Umoja", 350, -1.2790, 36.8890),
        ("Pipeline", 350, -1.3120, 36.8980),
        ("Nyayo Estate", 400, -1.3080, 36.8760),
    ]),
    ("Waiyaki Way", [
        ("Mountain View", 200, -1.2650, 36.7650),  # ambiguous ("Mutex Hill") - confirm
        ("Chiromo", 200, -1.2680, 36.8020),
        ("Saf Centre", 250, -1.2650, 36.7950),
        ("ABC Place", 300, -1.2650, 36.7900),
        ("Kangemi", 300, -1.2680, 36.7480),
        ("Kabete", 400, -1.2470, 36.7380),
        ("Uthiru/Kinoo", 400, -1.2560, 36.7180),
        ("Muthiga", 500, -1.2440, 36.6950),
        ("Kikuyu", 600, -1.2460, 36.6640),
    ]),
])


def area_choices():
    """
    [(zone_label, [(area_name, area_name), ...]), ...] shaped exactly
    how Django's ChoiceField/Select renders <optgroup>s, so the
    dropdown mirrors the chart zone-by-zone.
    """
    return [
        (zone, [(row[0], row[0]) for row in areas])
        for zone, areas in ZONES.items()
    ]


def _flat_lookup():
    lookup = {}
    for zone, areas in ZONES.items():
        for name, fee, lat, lng in areas:
            # Areas repeat under CBD-adjacent zones (e.g. "Ngara" appears
            # under both Thika Rd and Eastlands) with the same price in
            # every case on the chart, so first-seen wins and later
            # duplicates are silently skipped.
            lookup.setdefault(name, {
                "zone": zone,
                "fee": Decimal(fee),
                "lat": lat,
                "lng": lng,
            })
    return lookup


AREA_LOOKUP = _flat_lookup()


def is_valid_area(area_name):
    return area_name in AREA_LOOKUP


def fee_for_area(area_name):
    """Decimal KES shipping fee for a chosen area name, or None if unrecognised."""
    entry = AREA_LOOKUP.get(area_name)
    return entry["fee"] if entry else None


def zone_for_area(area_name):
    entry = AREA_LOOKUP.get(area_name)
    return entry["zone"] if entry else None


def area_data_for_js():
    """
    {area_name: {zone, fee, lat, lng}, ...} for the checkout page's map
    modal - it uses this to find the nearest priced area to whatever
    pin the customer drops, entirely client-side (no round trip needed
    for a ~90-row table). `fee` is cast to float since Decimal isn't
    JSON-serialisable.
    """
    return {
        name: {
            "zone": entry["zone"],
            "fee": float(entry["fee"]),
            "lat": entry["lat"],
            "lng": entry["lng"],
        }
        for name, entry in AREA_LOOKUP.items()
    }


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_area(lat, lng):
    """
    (area_name, distance_km) of the priced area closest to (lat, lng).
    Same great-circle matching the map modal's JS does, available here
    if you ever want to double-check or log what the client matched to.
    Returns (None, None) if ZONES is somehow empty.
    """
    best_name, best_dist = None, None
    for name, entry in AREA_LOOKUP.items():
        dist = _haversine_km(float(lat), float(lng), entry["lat"], entry["lng"])
        if best_dist is None or dist < best_dist:
            best_name, best_dist = name, dist
    return best_name, best_dist


def calculate_vat(subtotal):
    """
    16% VAT on the goods subtotal. Shipping is treated as a separate
    zero-rated delivery charge here, not part of the VAT base - if your
    accountant wants VAT charged on the fee too, add shipping_fee to
    the amount passed in before calling this.
    """
    return (Decimal(subtotal) * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)