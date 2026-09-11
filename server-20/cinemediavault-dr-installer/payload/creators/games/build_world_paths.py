#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "ne_110m_admin_0_countries.geojson"
OUT = ROOT / "assets" / "world-paths.js"
WIDTH = 1400
HEIGHT = 720

TERRITORY_RULES = {
    "canada": lambda p: p["ISO_A3"] == "CAN",
    "united_states": lambda p: p["ISO_A3"] == "USA",
    "mexico": lambda p: p["ISO_A3"] == "MEX",
    "central_america": lambda p: p["ISO_A3"] in {"BLZ", "GTM", "HND", "SLV", "NIC", "CRI", "PAN", "CUB", "HTI", "DOM", "JAM", "BHS", "TTO", "PRI"},
    "south_america": lambda p: p["CONTINENT"] == "South America",
    "greenland": lambda p: p["ISO_A3"] == "GRL",
    "iceland": lambda p: p["ISO_A3"] == "ISL",
    "britain": lambda p: p["ISO_A3"] in {"GBR", "IRL"},
    "scandinavia": lambda p: p["ISO_A3"] in {"NOR", "SWE", "FIN", "DNK"},
    "western_europe": lambda p: p["ISO_A3"] in {"FRA", "ESP", "PRT", "DEU", "NLD", "BEL", "LUX", "CHE", "AUT", "ITA"},
    "eastern_europe": lambda p: p["ISO_A3"] in {"POL", "CZE", "SVK", "HUN", "ROU", "BGR", "GRC", "ALB", "MKD", "SRB", "MNE", "BIH", "HRV", "SVN", "MDA", "UKR", "BLR", "EST", "LVA", "LTU"},
    "russia": lambda p: p["ISO_A3"] == "RUS",
    "middle_east": lambda p: p["ISO_A3"] in {"TUR", "SYR", "LBN", "ISR", "JOR", "IRQ", "IRN", "SAU", "YEM", "OMN", "ARE", "QAT", "KWT", "ARM", "AZE", "GEO", "CYP", "PSE"},
    "north_africa": lambda p: p["ISO_A3"] in {"MAR", "DZA", "TUN", "LBY", "EGY", "ESH", "MRT", "MLI", "NER", "TCD", "SDN", "ERI", "DJI", "SOM", "ETH"},
    "south_africa": lambda p: p["CONTINENT"] == "Africa" and p["ISO_A3"] not in {"MAR", "DZA", "TUN", "LBY", "EGY", "ESH", "MRT", "MLI", "NER", "TCD", "SDN", "ERI", "DJI", "SOM", "ETH"},
    "india": lambda p: p["ISO_A3"] in {"IND", "PAK", "BGD", "NPL", "BTN", "LKA"},
    "china": lambda p: p["ISO_A3"] in {"CHN", "MNG", "PRK", "KOR", "TWN"},
    "central_asia": lambda p: p["ISO_A3"] in {"KAZ", "UZB", "TKM", "KGZ", "TJK", "AFG"},
    "southeast_asia": lambda p: p["ISO_A3"] in {"MMR", "THA", "LAO", "KHM", "VNM", "MYS", "IDN", "PHL", "BRN", "TLS"},
    "japan": lambda p: p["ISO_A3"] == "JPN",
    "australia": lambda p: p["ISO_A3"] in {"AUS", "NZL", "PNG", "SLB", "VUT", "FJI", "NCL"},
}


def project(coord):
    lon, lat = coord[:2]
    x = (lon + 180.0) / 360.0 * WIDTH
    y = (90.0 - lat) / 180.0 * HEIGHT
    return round(x, 1), round(y, 1)


def ring_to_path(ring):
    if len(ring) < 3:
        return ""
    points = [project(c) for c in ring]
    if points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        return ""
    parts = [f"M{points[0][0]},{points[0][1]}"]
    parts.extend(f"L{x},{y}" for x, y in points[1:])
    parts.append("Z")
    return " ".join(parts)


def polygon_to_path(poly):
    return " ".join(filter(None, (ring_to_path(ring) for ring in poly)))


def geometry_to_path(geometry):
    if geometry["type"] == "Polygon":
        return polygon_to_path(geometry["coordinates"])
    if geometry["type"] == "MultiPolygon":
        return " ".join(filter(None, (polygon_to_path(poly) for poly in geometry["coordinates"])))
    return ""


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    paths = []
    territory_paths = {key: [] for key in TERRITORY_RULES}
    for feature in data["features"]:
        path = geometry_to_path(feature["geometry"])
        if path:
            props = feature.get("properties", {})
            paths.append({"name": props.get("NAME") or props.get("ADMIN") or "Country", "d": path})
            normalized = {k: props.get(k, "") for k in ("ISO_A3", "NAME", "ADMIN", "CONTINENT", "SUBREGION")}
            for territory_id, rule in TERRITORY_RULES.items():
                if rule(normalized):
                    territory_paths[territory_id].append(path)
                    break
    territory_paths = {key: " ".join(value) for key, value in territory_paths.items() if value}
    OUT.write_text(
        "window.WORLD_PATHS = "
        + json.dumps(paths, separators=(",", ":"))
        + ";\nwindow.TERRITORY_LAND_PATHS = "
        + json.dumps(territory_paths, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(paths)} country paths and {len(territory_paths)} territory groups to {OUT}")


if __name__ == "__main__":
    main()
