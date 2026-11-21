"""Project identity and third-party credits, shared by the web About tab and the screen menu."""

NAME = "Super Mesh Stack"
VERSION = "0.1.0"
LICENSE = "MIT"
AUTHOR = "2theMAX"
BYLINE = f"by {AUTHOR}"
REPO = "https://github.com/2theMAXtheStairFax/Super-Mesh-Stack"


def short_name(name):
    """First project of a combined credit, for the narrow screen list."""
    return name.split(" / ")[0].split(",")[0].strip()


# (name, what it provides, license, url)
CREDITS = [
    ("MeshCore", "Mesh protocol and companion radio firmware", "MIT", "https://github.com/meshcore-dev/MeshCore"),
    ("meshcore-cli / meshcore_py", "Python client library and CLI for the radio", "MIT", "https://github.com/fdlamotte/meshcore-cli"),
    ("Heltec T114", "nRF52840 / SX1262 radio hardware", "", "https://heltec.org/project/mesh-node-t114/"),
    ("MapLibre GL JS", "Map rendering in the Coverage tab", "BSD-3-Clause", "https://maplibre.org/"),
    ("Protomaps", "Vector basemap tiles, styles, sprites", "ODbL data, BSD-3 code", "https://protomaps.com/"),
    ("OpenStreetMap", "Map data, © OpenStreetMap contributors", "ODbL", "https://www.openstreetmap.org/copyright"),
    ("USGS National Map", "Topographic map tiles (United States)", "Public domain", "https://www.usgs.gov/programs/national-geospatial-program/national-map"),
    ("Noto Sans", "Map label fonts", "OFL-1.1", "https://fonts.google.com/noto"),
    ("Oswald, Bungee Hairline", "Web UI fonts", "OFL-1.1", "https://fonts.google.com/"),
    ("Bottle", "Web UI server", "MIT", "https://bottlepy.org/"),
    ("Pillow", "Screen rendering", "HPND", "https://python-pillow.org/"),
    ("OpenOCD", "SWD firmware flashing", "GPL-2.0-or-later", "https://openocd.org/"),
    ("Argon40", "Fan HAT installer", "", "https://argon40.com/"),
    ("Austin Mesh", "Bot etiquette guidelines", "", "https://www.austinmesh.org/join/bot-guidelines/"),
]
