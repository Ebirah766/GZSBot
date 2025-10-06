# bot.py
import os
import sys
import logging
import pathlib
import difflib
import re
import json  # <<< ADDED
from typing import Dict, Any, Tuple, Optional, List, Set  # <<< ADDED Set
import io  # <<< ADDED
import builtins

# --- Constants ---------------------------------------------------------------
# Render holdings with one line per holder (split comma-separated values into bullets)
REGION_ORDER = ["North America", "South America", "Europe", "Asia", "Africa", "Oceania", "Antarctica"]

def format_holdings(holdings: Dict[str, Any]) -> str:
    """
    Render each region as a header line, then bullets (always bullets, even for one item).
    Accepts per-region values like:
      - 0 / "0" / 0.0 / "" / None       -> 'Region: 0'
      - "1.1 - Zoo A, 0.3 - Zoo B"      -> header + bullets (split by commas)
      - ["1.1 - Zoo A", "0.3 - Zoo B"]  -> header + bullets
      - other scalars                    -> header + one bullet
    """
    lines: List[str] = []
    for region in REGION_ORDER:
        if region == "Antarctica":
            continue  # hidden in UI

        value = holdings.get(region, None)

        # Normalize "empty"
        if value in (None, "", 0, "0", 0.0):
            lines.append(f"**{region}:** 0")
            continue

        # Build a list of items to render
        if isinstance(value, (list, tuple, set)):
            items = [str(x).strip() for x in value if str(x).strip()]
        elif isinstance(value, str):
            # Split comma-separated strings into bullets
            items = [s.strip() for s in value.split(",") if s.strip()]
        else:
            items = [str(value).strip()]

        # Render (always bullets for consistency)
        if not items:
            lines.append(f"**{region}:** 0")
        else:
            lines.append(f"**{region}:**")
            lines.extend(f"• {it}" for it in items)

    return "\n".join(lines) if lines else "_No holdings data provided_"


# --- Logging setup -----------------------------------------------------------
LOG_FILE = pathlib.Path(__file__).with_name("bot.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("wotbp-bot")

# --- Optional dotenv (safe if not installed) --------------------------------
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
    log.info("Loaded environment from .env")
except Exception:
    log.info("python-dotenv not installed; skipping .env loader")

# >>> ADDED: keep_alive import <<<
from keep_alive import keep_alive

# --- Discord imports ---------------------------------------------------------
import discord
from discord.ext import commands
from discord.ext.commands import CommandNotFound

# --- Intents -----------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True

# --- Bot ---------------------------------------------------------------------
bot = commands.Bot(command_prefix=";", intents=intents)
log.info("Python exe: %s", sys.executable)
log.info("CWD: %s", os.getcwd())
log.info("DISCORD_TOKEN present? %s", "Yes" if os.getenv("DISCORD_TOKEN") else "No")

def format_holdings_lines(holdings: Dict[str, Any]) -> str:
    """
    Alternate renderer: one bullet per holder; shows single items inline.
    Accepts values like:
      - 0, "0", 0.0
      - "1.1 - Zoo A, 0.3 - Zoo B"      -> split into bullets
      - ["1.1 - Zoo A", "0.3 - Zoo B"]  -> bullets
    """
    lines: List[str] = []
    for region in REGION_ORDER:
        v = holdings.get(region, 0)

        # Normalize simple zeros
        if v in (0, "0", 0.0, None):
            lines.append(f"**{region}:** 0")
            continue

        # If it's a list/tuple/set, show one per line
        if isinstance(v, (list, tuple, set)):
            items = [str(x).strip() for x in v if str(x).strip()]
            if not items:
                lines.append(f"**{region}:** 0")
            elif len(items) == 1:
                lines.append(f"**{region}:** {items[0]}")
            else:
                lines.append(f"**{region}:**")
                lines.extend([f"• {it}" for it in items])
            continue

        # If it's a string, split by commas into items
        if isinstance(v, str):
            items = [s.strip() for s in v.split(",") if s.strip()]
            if not items:
                lines.append(f"**{region}:** 0")
            elif len(items) == 1:
                lines.append(f"**{region}:** {items[0]}")
            else:
                lines.append(f"**{region}:**")
                lines.extend([f"• {it}" for it in items])
            continue

        # Fallback: just print whatever it is
        lines.append(f"**{region}:** {v}")

    return "\n".join(lines)

# --- Token System (per-user, per-zoo) ---------------------------------------
TOKENS_FILE = pathlib.Path("tokens.json")
TOKENS_START = 10
_GLOBAL_KEY = "__global__"   # fallback while you migrate; optional

def _read_json(path: pathlib.Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def _write_json(path: pathlib.Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _ensure_structure(data: dict) -> dict:
    """
    Accepts your current shape:
      {"balances": {"334...": 10, "385...": 10}}
    and normalizes to:
      {"balances": {"334...": {"__global__": 10}, "385...": {"__global__": 10}}}
    while preserving any already-per-zoo dicts.
    """
    if not isinstance(data, dict):
        data = {}
    balances = data.get("balances") or {}
    if not isinstance(balances, dict):
        balances = {}
    normalized: dict = {}
    for uid, val in balances.items():
        if isinstance(val, dict):
            # already per-zoo
            normalized[str(uid)] = val
        elif isinstance(val, int):
            # migrate flat int -> per-zoo dict with a global fallback key
            normalized[str(uid)] = {_GLOBAL_KEY: int(val)}
        else:
            # unknown content -> start clean
            normalized[str(uid)] = {_GLOBAL_KEY: TOKENS_START}
    data["balances"] = normalized
    return data

def _load_tokens() -> dict:
    data = _read_json(TOKENS_FILE)
    return _ensure_structure(data)

def _save_tokens(data: dict):
    data = _ensure_structure(data)
    _write_json(TOKENS_FILE, data)

def _get_user_dict(data: dict, uid: int) -> dict:
    uid_s = str(uid)
    if "balances" not in data:
        data["balances"] = {}
    if uid_s not in data["balances"] or not isinstance(data["balances"][uid_s], dict):
        data["balances"][uid_s] = {_GLOBAL_KEY: TOKENS_START}
    return data["balances"][uid_s]

def get_user_zoo_tokens(uid: int, zoo: str | None) -> int:
    """
    If zoo is provided, return that zoo balance.
    If not provided, return the global/default (for display & fallback).
    When a zoo has no explicit balance yet, fall back to the user's global (or TOKENS_START).
    """
    data = _load_tokens()
    u = _get_user_dict(data, uid)
    if zoo:
        return int(u.get(zoo, u.get(_GLOBAL_KEY, TOKENS_START)))
    return int(u.get(_GLOBAL_KEY, TOKENS_START))

def set_user_zoo_tokens(uid: int, zoo: str, amount: int):
    data = _load_tokens()
    u = _get_user_dict(data, uid)
    u[zoo] = max(0, int(amount))
    _save_tokens(data)

def add_user_zoo_tokens(uid: int, zoo: str, delta: int):
    current = get_user_zoo_tokens(uid, zoo)
    set_user_zoo_tokens(uid, zoo, current + int(delta))

def spend_user_zoo_tokens(uid: int, zoo: str, amount: int) -> bool:
    """
    Atomically attempt to spend `amount` tokens from (uid, zoo).
    If sufficient, deduct and return True. Otherwise, no change and return False.
    """
    current = get_user_zoo_tokens(uid, zoo)
    if amount <= 0:
        return True
    if current < amount:
        return False
    set_user_zoo_tokens(uid, zoo, current - amount)
    return True

def list_user_zoos_with_balances(uid: int) -> list[tuple[str, int]]:
    """
    Returns a list of (zoo_name, tokens) excluding the __global__ key.
    """
    data = _load_tokens()
    u = _get_user_dict(data, uid)
    out = []
    for k, v in u.items():
        if k == _GLOBAL_KEY:
            continue
        try:
            out.append((k, int(v)))
        except Exception:
            pass
    return sorted(out, key=lambda kv: kv[0].lower())
    return sorted(out, key=lambda kv: kv[0].lower())


# --- Species DB --------------------------------------------------------------
REGIONS = ["North America", "South America", "Europe", "Asia", "Africa", "Oceania", "Antarctica"]

species_data: Dict[str, Dict[str, Any]] = {
    "Lion": {
        "common": "Lion",
        "scientific": "Panthera leo",
        "info": "Social big cat known for prides and a powerful roar.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Panthera",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/7/73/Lion_waiting_in_Namibia.jpg",
        "holdings": {
            "Africa": "Widespread in zoos",
            "Europe": "Many major zoos",
            "North America": "Common in AZA",
            "Asia": "Common",
            "South America": "Several zoos",
            "Oceania": "A few zoos",
        },
    },
    "Whale Shark": {
        "common": "Whale Shark",
        "scientific": "Rhincodon typus",
        "info": "The largest living fish; a gentle filter-feeding giant.",
        "type": "Fish",
        "order": "Orectolobiformes",
        "family": "Rhincodontidae",
        "genus": "Rhincodon",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f1/Whale_shark_Georgia_aquarium.jpg/1200px-Whale_shark_Georgia_aquarium.jpg",
        "holdings": {
            "North America": "Georgia Aquarium (notable)",
            "Asia": "Okinawa Churaumi (notable)",
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        # "images": [...]
    },
    "Mango Stem Borer": {
        "common": "Mango Stem Borer",
        "scientific": "Batocera maculata",
        "info": "A large longhorn beetle reaching 2.8 inches in length, the mango stem borer can be found in Southeast Asia. Adults of this species can be seen from April to August.",
        "type": "Invertebrate",
        "order": "Coleoptera",
        "family": "Cerambycidae",
        "genus": "Batocera",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d0/Batocera_maculata_%2833312343742%29.jpg/1200px-Batocera_maculata_%2833312343742%29.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "2 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": 2
        },
    },
    "Rhesus Macaque": {
        "common": "Rhesus Macaque",
        "scientific": "Macaca mulatta",
        "info": "Rhesus macaques are a well-known species of macaque native to Asia, from Afghanistan to China. They are exceptionally well-studied due to them being a common laboratory subject.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Cercopithecidae",
        "genus": "Macaca",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/d/d6/Rhesus_macaque_%28Macaca_mulatta_mulatta%29%2C_male%2C_Gokarna.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "2.4 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "2.4"
        },
    },
    "Housefly": {
        "common": "Housefly",
        "scientific": "Musca domestica",
        "info": "The housefly is a cosmopolitan and highly abundant species of fly. Its original range is unknown, but the most likely place is the Middle East. They are important scavengers, feeding commonly on carrion.",
        "type": "Invertebrate",
        "order": "Diptera",
        "family": "Muscidae",
        "genus": "Musca",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/79611144/original.jpeg",
        "region": "North America, South America, Europe, Asia, Africa, Oceania",
        "holdings": {
            "North America": "100 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "100"
        },
    },
    "Wild Boar": {
        "common": "Wild Boar",
        "scientific": "Sus scrofa",
        "info": "The wild boar is the ancestor of the modern domestic pig. It has a large range across 3 continents and is highly adaptable, typically living in loosely-associated herds.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Suidae",
        "genus": "Sus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Wildschwein%2C_N%C3%A4he_Pulverstampftor_%28cropped%29.jpg/1280px-Wildschwein%2C_N%C3%A4he_Pulverstampftor_%28cropped%29.jpg",
        "region": "Europe, Asia",
        "holdings": {
            "North America": "1.1 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "1.1"
        },
    },
    "American Mink": {
        "common": "American Mink",
        "scientific": "Neogale vison",
        "info": "A semi-aquatic mustelid native to much of North America, this species has been introduced outside of its native range and become invasive and destructive in Europe.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Mustelidae",
        "genus": "Neogale",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/American_Mink.jpg/1280px-American_Mink.jpg",
        "region": "North America",
        "holdings": {
            "North America": "1.0 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "1.0"
        },
    },
    "Water Buffalo": {
        "common": "Water Buffalo",
        "scientific": "Bubalus bubalis",
        "info": "The water buffalo is the domestic variant of the wild water buffalo. First domesticated in India, it has become a pack animal and source of food throughout the world, most commonly in Asia.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Bovidae",
        "genus": "Bubalus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bc/Water_buffalo_at_Rinca.jpg/1280px-Water_buffalo_at_Rinca.jpg",
        "holdings": {
            "North America": "2.4 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "2.4"
        },
    },
    "Florida Bass": {
        "common": "Florida Bass",
        "scientific": "Micropterus salmoides",
        "info": "Recently split from the largemouth bass, the Florida bass ranges throughout the Florida peninsula. It is extremely similar in appearance to the largemouth bass, with a few minor differences distinguishing the two.",
        "type": "Fish",
        "order": "Centrarchiformes",
        "family": "Centrarchidae",
        "genus": "Micropterus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/88108052/original.jpg",
        "region": "North America",
        "holdings": {
            "North America": "5 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "5"
        },
    },
    "Burmese Python": {
        "common": "Burmese Python",
        "scientific": "Python bivittatus",
        "info": "The Burmese python is one of the largest species of snakes. They grow up to 16ft and are considered an apex predator in their native range. They have been famously introduced to Florida where they have caused much damage.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Pythonidae",
        "genus": "Python",
        "images": [
            {"label": "Wild type", "url": "https://cdn.britannica.com/09/225209-050-5002E7F8/Burmese-python-invasive-species-captured-Everglades-National-Park-Florida.jpg"},
            {"label": "Piebald", "url": "https://community.morphmarket.com/uploads/db1442/original/3X/c/c/cce5c7bf05ff8c269b109b74a6981690acbc3075.jpeg"}
        ],
        "region": "Asia",
        "holdings": {
            "North America": "0.1 (Wild type) - Cube Zoological Park",
            "Asia": "1.0 (Piebald) - Sapporo Reptile Center and National Aquarium",
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "0.1 [Wild type]",
            "Sapporo Reptile Center and National Aquarium": "1.0 [Piebald]"
        },
    },
    "Burrowing Owl": {
        "common": "Burrowing Owl",
        "scientific": "Athene cunicularia",
        "info": "The burrowing owl is one of the smallest species of owls. Despite their common name, they do not burrow themselves, but rather appropriate other animal burrows.",
        "type": "Bird",
        "order": "Strigiformes",
        "family": "Strigidae",
        "genus": "Athene",
        "image_url": "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/205515041/1200",
        "region": "North America, South America",
        "holdings": {
            "North America": "1.1 - Cube Zoological Park, 0.3 - High Uintahs Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "1.1",
            "High Uintahs Zoo": "0.3"
        },
    },
    "Eastern Diamondback Rattlesnake": {
        "common": "Eastern Diamondback Rattlesnake",
        "scientific": "Crotalus adamanteus",
        "info": "The eastern diamondback rattlesnake is the largest rattlesnake species. It is endemic to the southeastern United States, and is one of the heaviest species of venomous snakes.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Crotalus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Adult_Crotalus_adamanteus.jpg/1280px-Adult_Crotalus_adamanteus.jpg",
        "region": "North America",
        "holdings": {
            "North America": "1.0 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "1.0"
        },
    },
    "Cheetah": {
        "common": "Cheetah",
        "scientific": "Acinonyx jubatus",
        "info": "The cheetah is one of the smaller big cat species, and the fastest amongst them. They are one of the most well-known and beloved species of animals, commonly displayed in zoos, though they are difficult to breed.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Acinonyx",
        "images": [
            {"label": "South African cheetah (jubatus)", "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/92/Male_cheetah_facing_left_in_South_Africa.jpg/1280px-Male_cheetah_facing_left_in_South_Africa.jpg"},
            {"label": "Variant 2 caption", "url": "https://example.com/variant2.jpg"},
            {"label": "Variant 3 caption", "url": "https://example.com/variant3.jpg"},
        ],
        "image_url": "https://example.com/default.jpg",
        "region": "Asia, Africa",
        "holdings": {
            "North America": 0,
            "Europe": ["0.0.1.0 (jubatus) - Shropshire Hills Zoo"],
            "Asia": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "0.0.1.0 [jubatus]"
        },
    },
    "Domestic Horse": {
        "common": "Domestic Horse",
        "scientific": "Equus caballus",
        "info": "The domestic horse is one of the most famous domesticated animals. Originating in Central Asia, they have spread to every continent in both domestic and feral forms.",
        "type": "Mammal",
        "order": "Perissodactyla",
        "family": "Equidae",
        "genus": "Equus",
        "images": [
            {"label": "Fjord", "url": "https://madbarn.com/wp-content/uploads/2023/07/Fjord-Horse-Breed-Guide-1.jpg"},
            {"label": "Variant 2 caption", "url": "https://example.com/variant2.jpg"},
            {"label": "Variant 3 caption", "url": "https://example.com/variant3.jpg"},
        ],
        "image_url": "https://example.com/default.jpg",
        "holdings": {
            "North America": 0,
            "Europe": ["1.3 (Fjord) - Shropshire Hills Zoo"],
            "Asia": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "1.3 [Fjord]"
        },
    },
    "Common Fallow Deer": {
        "common": "Common Fallow Deer",
        "scientific": "Dama dama",
        "info": "A very widespread and common deer species thought to have originated in the Mediterranean region, the common fallow deer is a mainstay in temperate and semi-arid environments across Eurasia.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Cervidae",
        "genus": "Dama",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/Fallow_deer_in_field.jpg/1280px-Fallow_deer_in_field.jpg",
        "region": "Europe, Asia",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "1.3 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "1.3"
        },
    },
    "Red Deer": {
        "common": "Red Deer",
        "scientific": "Cervus elaphus",
        "info": "The red deer is one of the largest deer species. Common throughout Europe, western Asia, and north Africa, males have impressive antlers which are grown and shed seasonally. They were introduced to various locations for hunting purposes.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Cervidae",
        "genus": "Cervus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/68001185/large.jpg",
        "region": "Europe, Asia, Africa",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "2.0 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "2.0"
        },
    },
    "Golden Lion Tamarin": {
        "common": "Golden Lion Tamarin",
        "scientific": "Leontopithecus rosalia",
        "info": "The golden lion tamarin is a highly endangered tamarin species endemic to the Atlantic coastal forests in southeastern Brazil. A large captive population is maintained in several countries as a safety net population.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Callitrichidae",
        "genus": "Leontopithecus",
        "image_url": "https://nationalzoo.si.edu/sites/default/files/animals/golden-lion-tamarin-001.jpg",
        "region": "South America",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "1.1 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "1.1"
        },
    },
    "Przewalski's Horse": {
        "common": "Przewalski's Horse",
        "scientific": "Equus przewalskii",
        "info": "The ancestor of the domestic horse, the Przewalski's horse once ranged across much of central, east, and north Asia. They were once highly endangered and are one of the first major captive breeding success stories.",
        "type": "Mammal",
        "order": "Perissodactyla",
        "family": "Equidae",
        "genus": "Equus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/398125195/large.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "0.1.0.1 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "0.1.0.1"
        },
    },
    "Red-Eyed Crocodile Skink": {
        "common": "Red-Eyed Crocodile Skink",
        "scientific": "Tribolonotus gracilis",
        "info": "A skink that is endemic to New Guinea, the red-eyed crocodile skink has obtained high popularity in the private reptile trade recently. They are rather sensitive if wild caught and captive-bred specimens are hardier.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Egerniidae",
        "genus": "Tribolonotus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/2/23/Red-Eyed_Crocodile_Skink.jpg",
        "region": "Oceania",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "1.1 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "1.1"
        },
    },
    "European Wildcat": {
        "common": "European Wildcat",
        "scientific": "Felis silvestris",
        "info": "A nocturnal wild felid that can be found from the United Kingdom to Azerbaijan. They are endangered in certain regions due to hybridization with domestic and feral cats.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Felis",
        "image_url": "https://www.biolib.cz/IMG/GAL/BIG/309949.jpg",
        "region": "Europe, Asia",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "0.1 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "0.1"
        },
    },
    "Arctic Fox": {
        "common": "Arctic Fox",
        "scientific": "Vulpes lagopus",
        "info": "The Arctic fox is found only in the northern polar regions, and is highly adapted for its environment, with thick fur and specialized physiological adaptations to handle the extreme cold.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Canidae",
        "genus": "Vulpes",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/388749029/original.jpg",
        "region": "North America, Europe, Asia",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "1.0 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "1.0"
        },
    },
    "Asian Small-Clawed Otter": {
        "common": "Asian Small-Clawed Otter",
        "scientific": "Aonyx cinereus",
        "info": "The Asian small-clawed otter is the smallest species of otter. They are exceptionally common in zoos due to a need for captive breeding, as the species is listed as Vulnerable in the wild.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Mustelidae",
        "genus": "Aonyx",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/14/Otter_-_melbourne_zoo.jpg/1280px-Otter_-_melbourne_zoo.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "0",
            "Asia": 0,
            "Europe": "1.1 - Shropshire Hills Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "1.1"
        },
    },
    "Elegant Crested Tinamou": {
        "common": "Elegant Crested Tinamou",
        "scientific": "Eudromia elegans",
        "info": "The elegant crested tinamou is a partridge-like bird native to Argentina's grasslands. During wintertime they live in groups and cover large territories together in search of food.",
        "type": "Bird",
        "order": "Tinamiformes",
        "family": "Tinamidae",
        "genus": "Eudromia",
        "image_url": "https://static.inaturalist.org/photos/28265534/large.jpg",
        "region": "South America",
        "holdings": {
            "North America": "2.2 - Credit River Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "2.2"
        },
    },
    "American Flamingo": {
        "common": "American Flamingo",
        "scientific": "Phoenicopterus ruber",
        "info": "Perhaps the most well-known and iconic flamingo species, the American flamingo can be found in North and South America as well as the Galapagos Islands. When they feed they turn their beaks upside down and filter feed with their beaks.",
        "type": "Bird",
        "order": "Phoenicopteriformes",
        "family": "Phoenicopteridae",
        "genus": "Phoenicopterus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/258687300/large.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": "5.5 - Credit River Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "5.5"
        },
    },
    "Southern Screamer": {
        "common": "Southern Screamer",
        "scientific": "Chauna torquata",
        "info": "The southern screamer has an extremely loud call which lends it its name. It can be heard from up to 2 miles away. They are generally found in wetlands and feed on various vegetation and seeds.",
        "type": "Bird",
        "order": "Anseriformes",
        "family": "Anhimidae",
        "genus": "Chauna",
        "image_url": "https://www.ecoregistros.org/site/images/dataimages/2018/10/02/289707/chaja-1.jpg",
        "region": "South America",
        "holdings": {
            "North America": "1.0 - Credit River Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "1.0"
        },
    },
    "American White Ibis": {
        "common": "American White Ibis",
        "scientific": "Eudocimus albus",
        "info": "The American white ibis can be found in coastal areas of North and South America. They gather in massive colonies during breeding season by the waterside and defend their nesting sites fiercely.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Threskiornithidae",
        "genus": "Eudocimus",
        "image_url": "https://www.biolib.cz/IMG/GAL/BIG/395236.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": "3.0 - Credit River Zoo",
            "Asia": 0,
             "Europe": 0,
             "Africa": 0,
             "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "3.0"
        },
    },
    "Roseate Spoonbill": {
        "common": "Roseate Spoonbill",
        "scientific": "Platalea ajaja",
        "info": "The roseate spoonbill, much like the flamingo, feeds on crustaceans in the water column by lapping them up. Their pink feather coloration comes from the astaxanthin in the crustaceans they consume.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Threskiornithidae",
        "genus": "Platalea",
        "image_url": "https://www.biolib.cz/IMG/GAL/BIG/561817.jpg",
        "region": "North America, South America",
        "holdings": {
             "North America": "0.2 - Credit River Zoo",
            "Asia": 0,
                "Europe": 0,
             "Africa": 0,
                "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "0.2"
        },
    },
    "Linnaeus's Two-Toed Sloth": {
        "common": "Linnaeus's Two-Toed Sloth",
        "scientific": "Choloepus didactylus",
        "info": "Linnaeus's two-toed sloth is the largest extant sloth species. They live in the rainforests of northern South America and are closely related to the extinct giant ground sloths.",
        "type": "Mammal",
        "order": "Pilosa",
        "family": "Choloepodidae",
        "genus": "Choloepus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/d/d4/Cholepus_didactylus_-_Flickr_-_Dick_Culbert.jpg",
        "region": "South America",
        "holdings": {
            "North America": "0.1 - Credit River Zoo",
              "Asia": 0,
            "Europe": 0,
                "Africa": 0,
                "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "0.1"
        },
    },
    "White-Faced Saki": {
        "common": "White-Faced Saki",
        "scientific": "Pithecia pithecia",
        "info": "The white-faced saki is a distinctive species of New World Monkey native to a small area of South America. The male possesses the distinctive white face, while the female has uniformly black-silver fur.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Pitheciidae",
        "genus": "Pithecia",
        "image_url": "https://www.marwell.org.uk/wp-content/uploads/2021/07/White-faced-saki-Pithecia-pithecia-Marwell-Zoo.jpg",
        "region": "South America",
        "holdings": {
            "North America": "1.1 - Credit River Zoo",
                "Asia": 0,
                "Europe": 0,
                "Africa": 0,
                "South America": 0,
                "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "1.1"
        },
    },
    "Cotton-Top Tamarin": {
        "common": "Cotton-Top Tamarin",
        "scientific": "Saguinus oedipus",
        "info": "A small, critically endangered New World monkey, the cotton-top tamarin is the subject of an extensive international breeding program. Their native range consists of a small patch of rainforest in Colombia.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Callitrichidae",
        "genus": "Saguinus",
        "image_url": "https://dwazoo.com/wp-content/uploads/2023/01/cotton2-scaled.jpg",
        "region": "South America",
        "holdings": {
            "North America": "1.3 - Credit River Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "1.3"
        },
    },
    "Black-Bellied Whistling Duck": {
        "common": "Black-Bellied Whistling Duck",
        "scientific": "Dendrocygna autumnalis",
        "info": "The black-bellied whistling duck is a medium to large sized duck species with a loud call. They live in large groups, and are monogamous, unusual for ducks.",
        "type": "Bird",
        "order": "Anseriformes",
        "family": "Anatidae",
        "genus": "Dendrocygna",
        "image_url": "https://www.pierrewildlife.com/wp-content/uploads/2024/03/Dendrocygna-autumnalis-fulgens-2.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": "0.4 - Credit River Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Credit River Zoo": "0.4"
        },
    },
    "Red-Rumped Agouti": {
        "common": "Red-Rumped Agouti",
        "scientific": "Dasyprocta leporina",
        "info": "The red-rumped agouti is an abundant rodent native to northeastern South America. They have been known to follow troops of monkeys in search of dropped food, benefitting off their diligent foraging.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Dasyproctidae",
        "genus": "Dasyprocta",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/8/8f/Red-rumped_Agouti_%2817380318590%29.jpg",
        "region": "South America",
        "holdings": {
            "North America": "0.2 - Credit River Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
                },
        "institutions": {
            "Credit River Zoo": "0.2"
        },
    },
    "Yellow-Naped Amazon": {
        "common": "Yellow-Naped Amazon",
        "scientific": "Amazona auropalliata",
        "info": "This amazon species is critically endangered due to deforestation and massive capture for the private trade. They are native to Central America and the last stronghold for their population is in Costa Rica.",
        "type": "Bird",
        "order": "Psittaciformes",
        "family": "Psittacidae",
        "genus": "Amazona",
        "image_url": "https://cdn.download.ams.birds.cornell.edu/api/v1/asset/44406711/1200",
        "region": "North America",
        "holdings": {
            "North America": "2.0 - Credit River Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
                        },
        "institutions": {
            "Credit River Zoo": "2.0"
        },
    },
    "Jaguar": {
        "common": "Jaguar",
        "scientific": "Panthera onca",
        "info": "The jaguar is the third largest of the big cats. One of the world's charismatic megafauna, they can be found in North and South America, and are an apex predator in their range, with strong commands of arboreal, terrestrial, and aquatic habitats.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Panthera",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/1/11/Jaguar_%28Panthera_onca_palustris%29_male_Three_Brothers_River_2_%28cropped%29.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "1.1 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "1.1"
        },
    },
    "White-Nosed Coati": {
        "common": "White-Nosed Coati",
        "scientific": "Nasua narica",
        "info": "The white-nosed coati ranges from the southwestern United States to Colombia. It is a highly adaptable species, able to live in a range of habitats, as well as being able to eat many types of food.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Procyonidae",
        "genus": "Nasua",
        "image_url": "https://www.zoochat.com/community/media/white-nosed-coati-nasua-narica.228154/full",
        "region": "North America, South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "1.3 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "1.3"
        }
    },
    "Chacoan Peccary": {
        "common": "Chacoan Peccary",
        "scientific": "Catagonus wagneri",
        "info": "The Chacoan peccary is one of three extant species of peccaries. For 4 decades it was thought to be extinct and is one of the most well-known examples of a Lazarus taxa. It is an endangered species due to expansion of ranching in its native range.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Tayassuidae",
        "genus": "Catagonus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/46/Catagonus_wagneri_1_-_Phoenix_Zoo.jpg/1280px-Catagonus_wagneri_1_-_Phoenix_Zoo.jpg",
        "region": "South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "2.2 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "2.2"
        }
    },
    "Geoffroy's Spider Monkey": {
        "common": "Geoffroy's Spider Monkey",
        "scientific": "Ateles geoffroyi",
        "info": "The Geoffroy's spider monkey is one of the largest New World monkeys, often weighing up to 20lbs. Found exclusively in Central America, this species is considered by some primatologists to be the third most intelligent primate species.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Atelidae",
        "genus": "Ateles",
        "images": [
            {"label": "Mexican Spider Monkey (vellerosus)", "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/Geoffroy%27s_spider_monkey_%28Ateles_geoffroyi_yucatanensis%29_Peten.jpg/1280px-Geoffroy%27s_spider_monkey_%28Ateles_geoffroyi_yucatanensis%29_Peten.jpg"}
        ],
        "region": "North America, South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "1.2 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "1.2"
        }
    },
    "Seba's Short-Tailed bat": {
        "common": "Seba's Short-Tailed bat",
        "scientific": "Carollia perspicillata",
        "info": "The Seba's short-taield bat is a common and widespread bat species that feeds on fruit. It is a generalist and will also consume nectar, pollen, and insects. They have a long lifespan, living up to 10 years.",
        "type": "Mammal",
        "order": "Chiroptera",
        "family": "Phyllostomidae",
        "genus": "Carollia",
        "image_url": "https://www.marylandzoo.org/wp-content/uploads/2017/10/bat_web.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "25 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "25"
    }
    },
    "Nine-Banded Armadillo": {
        "common": "Nine-Banded Armadillo",
        "scientific": "Dasypus novemcinctus",
        "info": "The nine-banded armadillo is the most common and by far most widespread armadillo species. Its range is actively expanding as the species migrates northward, being seen in the central United States more frequently than in the past.",
        "type": "Mammal",
        "order": "Cingulata",
        "family": "Dasypodidae",
        "genus": "Carollia",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/88373350/original.jpeg",
        "region": "North America, South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "1.1 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "1.1"

    }
    },
    "South American Tapir": {
        "common": "South American Tapir",
        "scientific": "Tapirus terrestris",
        "info": "The South American tapir is one of the 4 extant species of tapir. Native to large swathes of South America, this species is listed as Vulnerable on the IUCN Red List due to extensive habitat loss and poaching.",
        "type": "Mammal",
        "order": "Perissodactyla",
        "family": "Tapiridae",
        "genus": "Tapirus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/73961577/original.jpeg",
        "region": "South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "2.0 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "2.0"
        }
    },
    "Tayra": {
        "common": "Tayra",
        "scientific": "Eira barbara",
        "info": "Tayras are one of the larger mustelid species. Found throughout Central and South America, they are known to cache food such as fruit for later, making them one of the few animals to do so.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Mustelidae",
        "genus": "Eira",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/Tayra_-_Male%2C_Brazil_%28cropped%29.jpg/1280px-Tayra_-_Male%2C_Brazil_%28cropped%29.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "1.1 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "1.1"
        }
    },
    "Raccoon": {
        "common": "Raccoon",
        "scientific": "Procyon lotor",
        "info": "The most iconic procyonid, the raccoon is a nocturnal generalist and scavenger that is adapted to a wide range of habitats, including human-inhabited areas. They have been introduced to Europe and Asia and have become established there.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Procyonidae",
        "genus": "Procyon",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Raccoon_in_Central_Park_%2835264%29.jpg/1280px-Raccoon_in_Central_Park_%2835264%29.jpg",
        "region": "North America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "2.2 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "2.2"
        }
    },
    "Striped Skunk": {
        "common": "Striped Skunk",
        "scientific": "Mephitis mephitis",
        "info": "The striped skunk is the most well-known and widespread skunk species. A skittish nocturnal mesopredator, the striped skunk is known for its defense mechanism, where it sprays a foul-smelling liquid at threats.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Mephitidae",
        "genus": "Mephitis",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/61292683/large.jpg",
        "region": "North America",
        "holdings": {
            "North America": 0,
            "Asia": 0,
            "Europe": "0.1 - Mint Park Zoo",
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Mint Park Zoo": "0.1"
        }
    },
    "Beauty Rat Snake": {
        "common": "Beauty Rat Snake",
        "scientific": "Elaphe taeniura",
        "info": "A semi-arboreal species of snake native to Asia, the beauty rat snake has several recognized subspecies, with many different colorations. As a result, it can be difficult to identify them at a glance, unless one is well-versed in snakes.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Colubridae",
        "genus": "Elaphe",
        "images": [
            {"label": "Ridley's Cave Racer (ridleyi)", "url": "https://static.thainationalparks.com/img/species/2021/12/20/397706/elaphe-taeniurus-ridleyi-w-1500.jpg"}
        ],
        "region": "Asia",
        "holdings": {
            "North America": "1.1 [ridleyi] - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.1 [ridleyi]"
        }
    },
    "Eastern Indigo Snake": {
        "common": "Eastern Indigo Snake",
        "scientific": "Drymarchon couperi",
        "info": "Of all snakes native to the United States, the eastern indigo snake is the longest. This large colubrid is a high-ranking predator, feeding upon anything that can fit in its mouth, even venomous rattlesnakes, as it is immune to their venom.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Colubridae",
        "genus": "Drymarchon",
        "image_url": "https://www.fws.gov/sites/default/files/styles/facebook_1200x630/public/banner_images/2022-07/eastern-indigo-snake.jpg?h=f7c62170&itok=2StGVxab",
        "region": "North America",
        "holdings": {
            "North America": "1.1 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.1"
        }
    },
    "Black-Tailed Horned Pit Viper": {
        "common": "Black-Tailed Horned Pit Viper",
        "scientific": "Mixcoatlus melanurus",
        "info": "This endangered pit viper is native only to the mountains of southern Mexico. Its distinctive horns lend it the local common name of 'necazcoatl', literally meaning 'eared-serpent'.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Mixcoatlus",
        "image_url": "https://scontent-atl3-2.xx.fbcdn.net/v/t39.30808-6/472999940_1672789173595713_1159118854601384928_n.jpg?_nc_cat=105&ccb=1-7&_nc_sid=833d8c&_nc_ohc=YyUXLve42EkQ7kNvwHMtkmm&_nc_oc=AdlhzbHYMdP3n5QDbIe_fTpkWElKyz9d-Sxms3dbhBjG3oqODk1EfDlFaRL9asJovN66Z8G14SBgdBHI0i_WETrs&_nc_zt=23&_nc_ht=scontent-atl3-2.xx&_nc_gid=-hPNVuACjUTMk4Fuhlr6FA&oh=00_AffBxXIQB08HmECVF9ik8EnML5NASV72_7HaRDh2smC2qw&oe=68E7034D",
        "region": "North America",
        "holdings": {
            "North America": "0.2 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "0.2"
        }
    },
    "Indonesian Pit Viper": {
        "common": "Indonesian Pit Viper",
        "scientific": "Trimeresurus insularis",
        "info": "The Indonesian pit viper is a venomous snake species endemic to the Indonesian archipelago. They are arboreal, found in forests up to 3,900ft above sea level.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Trimeresurus",
        "images": [
            {"label": "Blue variant", "url": "https://media-animals.earth.com/images/2022/08/17/9637093228780143/trimeresurusinsularis_2552449653879538.jpg"},
            {"label": "Yellow variant", "url": "https://alephrocco.com/wp-content/uploads/2018/06/41776996854_0265ab2332_k.jpg"}
        ],
        "region": "Asia",
        "holdings": {
            "North America": "1.1 [Blue], 0.1 [Yellow] - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.1 [Blue], 0.1 [Yellow]"

            }
            },
    "Mangrove Pit Viper": {
        "common": "Mangrove Pit Viper",
        "scientific": "Trimeresurus purpureomaculatus",
        "info": "This venomous snake is native to South and Southeast Asia, from Bangladesh to Indonesia. Its unpredictable nature should be taken seriously, and its venom is highly toxic. Its coloration, like many Trimeresurus species, is highly variable.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Trimeresurus",
        "image_url": "https://static.thainationalparks.com/img/species/2017/07/05/317565/trimeresurus-purpureomaculatus-w-1500.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "0.3 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "0.3"

    }
    },
    "Wagler's Pit Viper": {
        "common": "Wagler's Pit Viper",
        "scientific": "Tropidolaemus wagleri",
        "info": "The Wagler's pit viper is also called the Wagler's temple viper, due to its abundance around the Temple of the Azure Cloud in Malaysia. This temple is also known as the Snake Temple due to this species' abundance in the area.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Tropidolaemus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/27/Tropidolaemus_wagleri%2C_Wagler%27s_palm_pit_viper_-_Takua_Pa_District%2C_Phang-nga_Province_%2848238132136%29.jpg/1280px-Tropidolaemus_wagleri%2C_Wagler%27s_palm_pit_viper_-_Takua_Pa_District%2C_Phang-nga_Province_%2848238132136%29.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "0.1 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "0.1"
        }
    },
    "Pygmy Rattlesnake": {
        "common": "Pygmy Rattlesnake",
        "scientific": "Sistrurus miliarius",
        "info": "One of the smallest rattlesnake species, the pygmy rattlesnake is endemic to the south and southeastern United States. It has multiple subspecies which have distinctive coloration, which makes them easy to identify.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Sistrurus",
        "images": [
            {"label": "Dusky Pygmy Rattlesnake (barbouri)", "url": "https://static.inaturalist.org/photos/392077465/large.jpeg"}
        ],
        "region": "North America",
        "holdings": {
            "North America": "1.2 [barbouri] - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.2 [barbouri]"
        }
    },
    "Sri Lankan Pit Viper": {
        "common": "Sri Lankan Pit Viper",
        "scientific": "Craspedocephalus trigonocephalus",
        "info": "These venomous snakes are arboreal and nocturnal, occasionally coming down to the ground to feed on animals such as lizards, frogs, small mammals, and birds. When agitated, this species will shake its tail tip, not unlike a rattlesnake.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Craspedocephalus",
        "image_url": "https://static.inaturalist.org/photos/352173492/large.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "1.1 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.1"
        }
    },
    "Egyptian Cobra": {
        "common": "Egyptian Cobra",
        "scientific": "Naja haje",
        "info": "One of the most venomous snakes in North Africa, the Egyptian cobra is prefers to prey on toads, which is unusual for a venomous snake. They are crepuscular/nocturnal and bask during the early morning to regain energy.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Elapidae",
        "genus": "Naja",
        "image_url": "https://static.inaturalist.org/photos/12833129/large.jpg",
        "region": "Africa",
        "holdings": {
            "North America": "1.1 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.1"
        }
    },
    "Western Mangrove Cat Snake": {
        "common": "Western Mangrove Cat Snake",
        "scientific": "Boiga melanota",
        "info": "This mildly venomous catsnake can be found in Thailand, Malaysia, Singapore, and Sumatra. Once subsumed under Boiga dendrophila, it was split from that species in 2020.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Colubridae",
        "genus": "Boiga",
        "image_url": "https://static.thainationalparks.com/img/species/2016/09/24/207594/boiga-melanota-w-1500.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "2.0 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "2.0"

        }
    },
    "Flat-Nosed Pit Viper": {
        "common": "Flat-Nosed Pit Viper",
        "scientific": "Craspedocephalus puniceus",
        "info": "A venomous snake native to Southeast Asia, the flat-nosed pit viper has a notoriously bad temper and strong venom which makes it a very dangerous species and respect must be afforded at all times.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Craspedocephalus",
        "images": [
            {"label": "Java locality", "url": "https://cdn.discordapp.com/attachments/1035388569419780176/1424050571932860416/large.png?ex=68e289f1&is=68e13871&hm=2a402427e2c62c390696e2a1653ea5cfb0c4ba8fc6368beb5425d7cf516c8911"}
        ],
        "region": "Asia",
        "holdings": {
            "North America": "1.1 [Java] - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.1 [Java]"

    }
    },
    "Monocled Cobra": {
        "common": "Monocled Cobra",
        "scientific": "Naja kaouthia",
        "info": "The monocled cobra is perhaps the most recognizable species of venomous snake in the world. Ranging throughout Southeast Asia, these snakes dwell in all types of habitat from forests to farmland.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Elapidae",
        "genus": "Naja",
        "images": [
            {"label": "Wild type", "url": "https://scontent-cph2-1.xx.fbcdn.net/v/t39.30808-6/491146798_1081368080686123_7102814473061028418_n.jpg?_nc_cat=101&ccb=1-7&_nc_sid=127cfc&_nc_ohc=dB_BPqbQrvgQ7kNvwHBWXOq&_nc_oc=Adl1lq2CaO7I_YK-PHlI7Ktskdbp_EN2ozE4azVJgWZdVzP4R5o1o9f-Xcm10MsQONM&_nc_zt=23&_nc_ht=scontent-cph2-1.xx&_nc_gid=lfz1apkHFuI_dmAdS9v5VA&oh=00_AffAfUf6YWEMRyAQffeHdVRUJ3s4PtJZF4cXosCvcEeqAQ&oe=68E7251E"},
            {"label": "Leucistic", "url": "https://scontent-cph2-1.xx.fbcdn.net/v/t39.30808-6/510343141_9936717456440041_3752577244402236551_n.jpg?stp=dst-jpg_p843x403_tt6&_nc_cat=107&ccb=1-7&_nc_sid=0b6b33&_nc_ohc=fTmB3GAJFVkQ7kNvwEDuJ19&_nc_oc=AdnmGnVQfKM0NTljvtzn1JN0g1vCZCOE6U1Dgi-JK2ekB_uqV9ilC0nUW_LT5yFa9ZM&_nc_zt=23&_nc_ht=scontent-cph2-1.xx&_nc_gid=r9UN0pTHtEO9xWBJGuJxJg&oh=00_AfegHTmllnIDkiBl44dtRWx877XeyA8qaUBljjAPlb_rTQ&oe=68E6FB38"}
        ],
        "region": "Asia",
        "holdings": {
            "North America": "1.0 [Wild type] 0.1 [Leucistic] - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.0 [Wild type], 0.1 [Leucistic]"
        }
    },
    "Eastern Coral Snake": {
        "common": "Eastern Coral Snake",
        "scientific": "Micrurus fulvius",
        "info": "The eastern coral snake is a highly venomous snake species native to the southeastern United States. Its highly venomous nature has lent it the occasional common name 'American cobra'.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Elapidae",
        "genus": "Micrurus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/105813097/large.jpeg",
        "region": "North America",
        "holdings": {
            "North America": "1.1 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.1"
        }
    },
    "Blood Python": {
        "common": "Blood Python",
        "scientific": "Python brongersmai",
        "info": "The blood python is native to the Sumatra and Malay Peninsula. It is best known for its vivid red, orange, and brown coloration, which makes it one of the most striking species in the python family.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Pythonidae",
        "genus": "Python",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/94/Python_brongersmai%2C_Brongersma%27s_short-tailed_python.jpg/1200px-Python_brongersmai%2C_Brongersma%27s_short-tailed_python.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "0.1 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "0.1"
        }
    },
    "Chinese Crocodile Lizard": {
        "common": "Chinese Crocodile Lizard",
        "scientific": "Shinisaurus crocodilurus",
        "info": "This living fossil species of lizard can be found exclusively in southern China and northern Vietnam. They are semi-aquatic, claiming a section of pond or stream for themselves, and feed upon small animals like insects, small fish, and frogs.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Shinisauridae",
        "genus": "Shinisaurus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d7/Shinisaurus_crocodilurus_10.jpg/1280px-Shinisaurus_crocodilurus_10.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "1.2 - Jupiter Reptile Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Jupiter Reptile Zoo": "1.2"
        }
    },
    "Bighorn Sheep": {
        "common": "Bighorn Sheep",
        "scientific": "Ovis canadensis",
        "info": "The bighorn sheep is an iconic species of sheep found throughout alpine regions of western North America, from Canada to Mexico. Several subspecies exist, each adapted for a specific region and climate.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Bovidae",
        "genus": "Ovis",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/New_Mexico_Bighorn_Sheep.JPG/1280px-New_Mexico_Bighorn_Sheep.JPG",
        "region": "North America",
        "holdings": {
            "North America": "0.4 - High Uintahs Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "High Uintahs Zoo": "0.4"
        }
    },
    "Mountain Lion": {
        "common": "Mountain Lion",
        "scientific": "Puma concolor",
        "info": "One of two big cats native to the Americas, the mountain lion is highly adaptable and found in a variety of habitats, including mountains, deserts, and rainforests. They are one of the most iconic and beloved species found in the Americas.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Puma",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/9834553/original.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": "0.1 - High Uintahs Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
                },
        "institutions": {
            "High Uintahs Zoo": "0.1"
        }
    },
    "North American Porcupine": {
         "common": "North American Porcupine",
        "scientific": "Erethizon dorsatum",
        "info": "The North American porcupine is a large, aboreal rodent native to North America, from northern Canada to central Mexico. It is the second largest rodent in North America after the North America beaver.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Erethizontidae",
        "genus": "Erethizon",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/8/8c/Erethizon_dorsatum_-_Prince_Rupert.jpg",
        "region": "North America",
        "holdings": {
            "North America": "1.1 - High Uintahs Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
                        },
        "institutions": {
            "High Uintahs Zoo": "1.1"

            }
            },
    "Bobcat": {
        "common": "Bobcat",
        "scientific": "Lynx rufus",
        "info": "One of four lynx species, the bobcat can be found throughout North America. It is a specialized lagomorph hunter, though it will take other prey if possible. They are likely the most abundant and common lynx species.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Lynx",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/60028871/original.jpg",
        "region": "North America",
        "holdings": {
            "North America": "1.1 - High Uintahs Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
                        },
        "institutions": {
            "High Uintahs Zoo": "1.1"

            }
            },
    "Gray Fox": {
        "common": "Gray Fox",
        "scientific": "Urocyon cinereoargenteus",
        "info": "The gray fox is found in both North and South America in a wide variety of habitats. Adaptable like most foxes, it plays a role as an important mesopredator, keeping rodent populations from getting too high.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Canidae",
        "genus": "Urocyon",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a3/Gray_fox.jpg/1280px-Gray_fox.jpg",
        "region": "North America, South America",
        "holdings": {
            "North America": "1.1 - High Uintahs Zoo",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
                        },
        "institutions": {
            "High Uintahs Zoo": "1.1"

            }
            },
    
    "Turkey Vulture": {
    "common": "Turkey Vulture",
    "scientific": "Cathartes aura",
    "info": "Turkey vultures are the most widespread New World vulture species. An abundant and highly adaptable animal, turkey vultures can be found in most areas of the Americas, except certain mountainous regions of South America and taiga/polar areas of North America.",
    "type": "Bird",
    "order": "Accipitriformes",
    "family": "Cathartidae",
    "genus": "Cathartes",
    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/171981785/original.jpg",
    "region": "North America, South America",
    "holdings": {
        "North America": "2.0 - High Uintahs Zoo",
        "Asia": 0,
        "Europe": 0,
        "Africa": 0,
        "South America": 0,
        "Oceania": 0,
                        },
    "institutions": {
            "High Uintahs Zoo": "2.0"

        }
        },

    "Northern Flying Squirrel": {
    "common": "Northern Flying Squirrel",
    "scientific": "Glaucomys sabrinus",
    "info": "One of three North American flying squirrel species, the northern flying squirrel can be found from Alaska to the Carolinas. It can live in a variety of habitats, and unusual for a squirrel, its major food source is fungi of various species.",
    "type": "Mammal",
    "order": "Rodentia",
    "family": "Sciuridae",
    "genus": "Glaucomys",
    "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/Northern_Flying_Squirrel%2C_D%27Alembert%2C_6400_Route_d%27Aiguebelle%2C_Rouyn-Noranda%2C_QC%2C_Canada_imported_from_iNaturalist_photo_41110662.jpg/1280px-Northern_Flying_Squirrel%2C_D%27Alembert%2C_6400_Route_d%27Aiguebelle%2C_Rouyn-Noranda%2C_QC%2C_Canada_imported_from_iNaturalist_photo_41110662.jpg",
    "region": "North America",
    "holdings": {
        "North America": "1.1 - High Uintahs Zoo",
        "Asia": 0,
        "Europe": 0,
        "Africa": 0,
        "South America": 0,
        "Oceania": 0,
                    },
    "institutions": {
        "High Uintahs Zoo": "1.1"

        }
        },
        
    "Domestic Donkey": {
    "common": "Domestic Donkey",
    "scientific": "Equus africanus asinus",
    "info": "The domestic donkey is one of two domesticated equids. These animals are mostly used as beasts of burden or pack animals, and are especially commonly used in underdeveloped countries.",
    "type": "Mammal",
    "order": "Perissodactyla",
    "family": "Equidae",
    "genus": "Equus",
    "image_url": "https://static.wikia.nocookie.net/project-zoo/images/9/90/Perry-miniature-donkey-in-Palo-Alto-CA-2016.jpg/revision/latest/scale-to-width-down/4559?cb=20200320040740",
    "holdings": {
        "North America": "4.0 - High Uintahs Zoo",
        "Asia": 0,
        "Europe": 0,
        "Africa": 0,
        "South America": 0,
        "Oceania": 0,
                        },
    "institutions": {
        "High Uintahs Zoo": "4.0"

        }
        },

"Common Raven": {
"common": "Common Raven",
"scientific": "Corvus corax",
"info": "The common raven is one of the largest corvids. Native to a wide range of habitats, this passerine bird is regarded for its high intelligence, and it is considered one of the most intelligent of all birds.",
"type": "Bird",
"order": "Passeriformes",
"family": "Corvidae",
"genus": "Corvus",
"image_url": "https://media-animals.earth.com/images/2022/08/17/6615221388313479/corvuscorax_31777344837356836.jpg",
"region": "North America, Europe, Asia, Africa",
"holdings": {
"North America": "1.0 - High Uintahs Zoo",
"Asia": 0,
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"High Uintahs Zoo": "1.0"

}
},

"Arapaima": {
"common": "Arapaima",
"scientific": "Arapaima gigas",
"info": "The largest bonytongue fish, the arapaima is an apex predator in the Amazon ecosystem. These massive fish protect their young vigorously until they are a substantial size, and can live up to 20 years.",
"type": "Fish",
"order": "Osteoglossiformes",
"family": "Arapaimidae",
"genus": "Arapaima",
"image_url": "https://www.biolib.cz/IMG/GAL/BIG/493019.jpg",
"region": "South America",
"holdings": {
"North America": 0,
"Asia": "0.1 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
        },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "0.1"

    }
    },
    
"Japanese Eagle Ray": {
"common": "Japanese Eagle Ray",
"scientific": "Myliobatis tobijei",
"info": "The Japanese eagle ray is a large species of eagle ray that can grow up to 5 feet in length. It is a demersal species, feeding on benthic animals such as crustaceans, fish, and on occasion, benthic plants.",
"type": "Fish",
"order": "Myliobatiformes",
"family": "Myliobatidae",
"genus": "Myliobatis",
"image_url": "https://www.marinepia.or.jp/picturebook/public/image/upload/594/main01.jpg",
"region": "Asia",
"holdings": {
"North America": 0,
"Asia": "0.3 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
            },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "0.3"

}
},

"Spotted Garden Eel": {
"common": "Spotted Garden Eel",
"scientific": "Heteroconger hassi",
"info": "Probably the most famous species of garden eel, the spotted garden eel is found throughout the Indo-Pacific, commonly in large aggregations in sandy areas. They rarely, if ever leave their burrows after they dig them.",
"type": "Fish",
"order": "Anguilliformes",
"family": "Congridae",
"genus": "Heteroconger",
"image_url": "https://images.reeflifesurvey.com/0/species_f4_57f745ed20d77.w1000.h666.jpg",
"region": "Asia, Africa, Oceania",
"holdings": {
"North America": 0,
"Asia": "2.4 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "2.4"

}
},

"Silver Arowana": {
"common": "Silver Arowana",
"scientific": "Osteoglossum bicirrhosum",
"info": "The silver arowana is a species of bonytongue fish native to South America. It is well known for its powerful jumping ability, which allows it to jump out of the water to hunt its preferred prey.",
"type": "Fish",
"order": "Osteoglossiformes",
"family": "Osteoglossidae",
"genus": "Osteoglossum",
"image_url": "https://upload.wikimedia.org/wikipedia/commons/0/0f/Osteoglossum_bicirrhosum_in_Minsk_Zoo.jpg",
"region": "South America",
"holdings": {
"North America": 0,
"Asia": "1.1 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "1.1"

}
},
    
"Red-Bellied Piranha": {
"common": "Red-Bellied Piranha",
"scientific": "Pygocentrus nattereri",
"info": "This iconic species of freshwater fish is native to South America, and has an unjust reputation for being an aggressive man-eater. In reality, they are typically solitary and only congregate and aggressively feed during the dry season.",
"type": "Fish",
"order": "Characiformes",
"family": "Serrasalmidae",
"genus": "Pygocentrus",
"image_url": "https://www.biolib.cz/IMG/GAL/BIG/382042.jpg",
"region": "South America",
"holdings": {
"North America": 0,
"Asia": "2.2 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "2.2"

}
},

"Axolotl": {
"common": "Axolotl",
"scientific": "Ambystoma mexicanum",
"info": "One of the most famous and beloved salamanders, the axolotl is (or was) endemic to a few lakes in the Mexico City area. It is heavily endangered due to development in its former range, and may be extinct in the wild.",
"type": "Amphibian",
"order": "Urodela",
"family": "Ambystomatidae",
"genus": "Ambystoma",
"image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/00/Axolotl_ganz.jpg/1920px-Axolotl_ganz.jpg",
"region": "North America",
"holdings": {
"North America": 0,
"Asia": "1.0 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "1.0"

}
},
    
"American Alligator": {
"common": "American Alligator",
"scientific": "Alligator mississippiensis",
"info": "One of the world's charismatic megafauna, the American alligator is found exclusively in the subtropical parts of North America. It was thought to be endemic to the United States, but there are unconfirmed sightings in northern Mexico.",
"type": "Reptile",
"order": "Crocodilia",
"family": "Alligatoridae",
"genus": "Alligator",
"image_url": "https://a-z-animals.com/media/animals/images/original/Alligator_mississippiensis_1-1.jpg",
"region": "North America",
"holdings": {
"North America": 0,
"Asia": "0.1 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "0.1"

}
},

"Chinese Alligator": {
"common": "Chinese Alligator",
"scientific": "Alligator sinensis",
"info": "The smaller of the two extant species of alligator, the Chinese alligator is critically endangered due to extensive habitat loss. Unlike the American alligator, they typically feed on small animals like snails and clams.",
"type": "Reptile",
"order": "Crocodilia",
"family": "Alligatoridae",
"genus": "Alligator",
"image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cb/ChineseAlligator15.JPG/1280px-ChineseAlligator15.JPG",
"region": "Asia",
"holdings": {
"North America": 0,
"Asia": "0.1 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "0.1"

}
},

"Veiled Chameleon": {
"common": "Veiled Chameleon",
"scientific": "Chamaeleo calyptratus",
"info": "This is one of the most well-known and commonly kept chameleon species. Native to the Arabian Peninsula, this species is born pastel green and without its distinctive casque, which it grows later in life.",
"type": "Reptile",
"order": "Squamata",
"family": "Chamaeleonidae",
"genus": "Chamaeleo",
"image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/68/Yemen_Chameleon.jpg/1280px-Yemen_Chameleon.jpg",
"region": "Asia",
"holdings": {
"North America": 0,
"Asia": "1.2 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "1.2"

}
},

"Fly River Turtle": {
"common": "Fly River Turtle",
"scientific": "Carettochelys insculpta",
"info": "Perhaps the most unique of all freshwater turtles, the Fly River turtle also goes by the common name of 'pig-nosed turtle' for its distinctive nose, which it uses to breathe in air when fully submerged.",
"type": "Reptile",
"order": "Testudines",
"family": "Carettochelyidae",
"genus": "Carettochelys",
"image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bb/Carettochelys_insculpta_01.JPG/1920px-Carettochelys_insculpta_01.JPG",
"region": "Oceania",
"holdings": {
"North America": 0,
"Asia": "2.2 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "2.2"

}
},

"Argentine Black and White Tegu": {
"common": "Argentine Black and White Tegu",
"scientific": "Salvator merianae",
"info": "The largest of the tegus, the Argentine black and white tegu is also the most commonly kept as a pet. They are highly intelligent for lizards, and are similar to monitor lizards in behavior, but are not closely related at all.",
"type": "Reptile",
"order": "Squamata",
"family": "Teiidae",
"genus": "Salvator",
"image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Black_and_White_Tegu.jpg/1920px-Black_and_White_Tegu.jpg",
"region": "South America",
"holdings": {
"North America": 0,
"Asia": "1.0 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "1.0"

    }
    },
    
"Green Iguana": {
"common": "Green Iguana",
"scientific": "Iguana iguana",
"info": "The most well-known of iguanas, the green iguana is a large lizard native to tropical regions of the Americas. They are primarily herbivorous, and debate exists about whether they ever intentionally consume animal protein.",
"type": "Reptile",
"order": "Squamata",
"family": "Iguanidae",
"genus": "Iguana",
"image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/356025444/original.jpg",
"region": "North America, South America",
"holdings": {
"North America": 0,
"Asia": "1.2 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "1.2"

        }
        },

"Alligator Snapping Turtle": {
"common": "Alligator Snapping Turtle",
"scientific": "Macrochelys temminckii",
"info": "One of the largest freshwater turtle species, the alligator snapping turtle is an apex predator in its range, feeding upon anything it can catch. It is an ambush predator, using its worm-like tongue to lure in prey, but will opportunistically take prey in other ways.",
"type": "Reptile",
"order": "Testudines",
"family": "Chelydridae",
"genus": "Macrochelys",
"image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/12726192/original.jpg",
"region": "North America",
"holdings": {
"North America": 0,
"Asia": "1.0 - Sapporo Reptile Center and National Aquarium ",
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
                },
"institutions": {
"Sapporo Reptile Center and National Aquarium": "1.0"

    }
    },
    "Green Tree Python": {
        "common": "Green Tree Python",
        "scientific": "Morelia viridis",
        "info": "The green tree python is an arboreal snake species native to Australasia. Interestingly, it has convergently evolved with the emerald tree boas of South America (in their behavior and habitat), but is not closely related to them at all.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Pythonidae",
        "genus": "Morelia",
        "images": [
            {"label": "High Blue", "url": "https://community.morphmarket.com/uploads/db1442/original/3X/e/a/ea18e345c3aa76c632b2227dc66a70d4cd7ad613.jpeg"},
        ],
        "region": "Oceania",
        "holdings": {
            "North America": 0,
            "Asia": "1.1 (High Blue) - Sapporo Reptile Center and National Aquarium",
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Sapporo Reptile Center and National Aquarium": "1.1 [High Blue]"

            }
            },

"Spotfin Betta": {
"common": "Spotfin Betta",
"scientific": "Betta macrostoma",
"info": "A large betta species growing up to 4 inches, the spotfin betta is endemic to the island of Borneo. Prized for its size and beauty, this species is heavily poached from the wild for the private aquarium trade, and is listed as Vulnerable on the IUCN Red List.",
"type": "Fish",
"order": "Anabantiformes",
"family": "Osphronemidae",
"genus": "Betta",
"image_url": "https://www.fishi-pedia.com/wp-content/uploads/2016/09/1781411_793531127324260_5255704950338882941_o.jpg",
"region": "Asia",
"holdings": {
"North America": "1.1 - New York Aquarium ",
"Asia": 0,
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
        },
"institutions": {
"New York Aquarium": "1.1"

    }
    },

"Krabi Mouth-Brooding Betta": {
"common": "Krabi Mouth-Brooding Betta",
"scientific": "Betta simplex",
"info": "This heavily endangered betta species is endemic to one location in Krabi Province, Thailand. Thankfully it is very hardy in captivity and breeds well, leading to a captive population in public aquaria and private collections.",
"type": "Fish",
"order": "Anabantiformes",
"family": "Osphronemidae",
"genus": "Betta",
"image_url": "https://media-animals.earth.com/images/2018/12/18/15475565562648463/bettasimplex.jpg",
"region": "Asia",
"holdings": {
"North America": "3.3 - New York Aquarium ",
"Asia": 0,
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
        },
"institutions": {
"New York Aquarium": "3.3"

    }
    },
    
    "Bluefin Nothobranchius": {
        "common": "Bluefin Nothobranchius",
        "scientific": "Nothobranchius rachovii",
        "info": "Probably the most popular Nothobranchius killifish to keep in captivity, the bluefin nothobranchius is native exclusively to Mozambique. Like all Nothobranchius, they live in annual pools where they lay their eggs in the mud just before the dry season.",
        "type": "Fish",
        "order": "Cyprinodontiformes",
        "family": "Nothobranchiidae",
        "genus": "Nothobranchius",
        "images": [
            {"label": "Beira locality", "url": "https://scontent-atl3-3.xx.fbcdn.net/v/t39.30808-6/511261235_23997244009907137_7942919226440147774_n.jpg?_nc_cat=110&ccb=1-7&_nc_sid=cf85f3&_nc_ohc=BfAABtDfAKUQ7kNvwFKTAhG&_nc_oc=AdnQQaH7Tle9DKYa2hJF21A5DrBmN5kuMnTiW3d3FcXhBlF1WwFbmoY9x0keasyhz9FSFW-isWpsW61M9hJ6QMbk&_nc_zt=23&_nc_ht=scontent-atl3-3.xx&_nc_gid=5s6uw2AlUR0CSWWz4WhRDQ&oh=00_Afe0ekUvmE195L5k8hdBa8UDgLrJeCykFkEsACA44IqAjQ&oe=68E848E0"},
        ],
        "region": "Africa",
        "holdings": {
            "North America": "3.3 (Beira) - New York Aquarium",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "New York Aquarium": "3.3 [Beira]"

            }
            },
            
    "Redtail Nothobranchius": {
        "common": "Redtail Nothobranchius",
        "scientific": "Nothobranchius guentheri",
        "info": "This endangered Nothobranchius species is endemic to Zanzibar in Tanzania. It is endangered due to habitat loss in its native range, and is a voracious predator of mosquito larvae. Scientists are looking into introducing this species to other parts of Africa to help control mosquito populations.",
        "type": "Fish",
        "order": "Cyprinodontiformes",
        "family": "Nothobranchiidae",
        "genus": "Nothobranchius",
        "images": [
            {"label": "Zanzibar locality", "url": "https://killis.org.uk/wp-content/uploads/2023/10/Nothobranchius-guentheri-Zanzibar.jpg"},
        ],
        "region": "Africa",
        "holdings": {
            "North America": "3.3 (Zanzibar) - New York Aquarium",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
            "institutions": {
            "New York Aquarium": "3.3 [Zanzibar]"

                }
                },
                
        "Common Carp": {
        "common": "Common Carp",
        "scientific": "Cyprinus carpio",
        "info": "An extremely hardy cyprinid, the common carp is famous (or infamous) for its adaptability. A generalist, common carp will eat just about anything, and can survive adverse conditions so well that they are one of the most notorious invasive species globally.",
        "type": "Fish",
        "order": "Cypriniformes",
        "family": "Cyprinidae",
        "genus": "Cyprinus",
        "image_url": "https://cdn.britannica.com/34/199834-050-ACEB68C7/Carp.jpg",
        "region": "Europe, Asia",
        "holdings": {
        "North America": "10 - New York Aquarium ",
        "Asia": 0,
        "Europe": 0,
        "Africa": 0,
        "South America": 0,
        "Oceania": 0,
                },
        "institutions": {
        "New York Aquarium": "10"

            }
            },
            
        "Brook Trout": {
        "common": "Brook Trout",
        "scientific": "Salvelinus fontinalis",
        "info": "Technically a char and not a trout, the brook trout can be found natively in eastern North America, in the United States and Canada. They have been introduced to various locations outside of their native, and are paradoxically threatened in their native range due to pollution, habitat loss, invasive species, and damming.",
        "type": "Fish",
        "order": "Salmoniformes",
        "family": "Salmonidae",
        "genus": "Salvelinus",
        "image_url": "https://media-animals.earth.com/images/2022/08/17/9385017317518864/salvelinusfontinalis_6479630421282603.jpg",
        "region": "North America",
        "holdings": {
        "North America": "20 - New York Aquarium ",
        "Asia": 0,
        "Europe": 0,
        "Africa": 0,
        "South America": 0,
        "Oceania": 0,
                },
        "institutions": {
        "New York Aquarium": "20"

            }
            },

    "Brown Trout": {
    "common": "Brown Trout",
    "scientific": "Salmo trutta",
    "info": "A familiar fish to anglers globally, the brown trout is the most widely distributed member of its genus. They have been introduced to many different locations, including North America, Australia, New Zealand, India, and even the Kerguelen Islands.",
    "type": "Fish",
    "order": "Salmoniformes",
    "family": "Salmonidae",
    "genus": "Salmo",
    "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/88/Salmo_trutta_Ozeaneum_Stralsund_HBP_2010-07-02.jpg/1280px-Salmo_trutta_Ozeaneum_Stralsund_HBP_2010-07-02.jpg",
    "region": "Europe, Asia, Africa",
    "holdings": {
    "North America": "20 - New York Aquarium ",
    "Asia": 0,
    "Europe": 0,
    "Africa": 0,
    "South America": 0,
    "Oceania": 0,
        },
    "institutions": {
    "New York Aquarium": "20"

        }
        },
        
        "Rainbow Trout": {
        "common": "Rainbow Trout",
        "scientific": "Oncorhynchus mykiss",
        "info": "One of the most renowned gamefish in the world, the rainbow trout, like many of its cousins, has been introduced to areas outside of its native range, such as Europe, South America, and New Zealand. They are an intensely studied species with several ecotypes, including the endangered steelhead.",
        "type": "Fish",
        "order": "Salmoniformes",
        "family": "Salmonidae",
        "genus": "Oncorhynchus",
        "image_url": "https://www.fishi-pedia.com/wp-content/uploads/2024/12/Oncorhynchus-mykiss-BCH-FISHI-Aquarium-scaled.jpg",
        "region": "North America, Asia",
        "holdings": {
        "North America": "20 - New York Aquarium ",
        "Asia": 0,
        "Europe": 0,
        "Africa": 0,
        "South America": 0,
        "Oceania": 0,
            },
        "institutions": {
        "New York Aquarium": "20"

            }
            },

    "China Rockfish": {
    "common": "China Rockfish",
    "scientific": "Sebastes nebulosus",
    "info": "The China rockfish can be found not in China, but rather the eastern Pacific Ocean, from Alaska to southern California. Typical for rockfish, it inhabits reefs between 3 and 128 meters, but is rarely seen below 92 meters.",
    "type": "Fish",
    "order": "Perciformes",
    "family": "Scorpaenidae",
    "genus": "Sebastes",
    "image_url": "https://www.oceanlight.com/stock-photo/china-rockfish-picture-14041-900602.jpg",
    "region": "North America",
    "holdings": {
    "North America": "6 - New York Aquarium ",
    "Asia": 0,
    "Europe": 0,
    "Africa": 0,
    "South America": 0,
    "Oceania": 0,
        },
    "institutions": {
    "New York Aquarium": "6"

        }
        },

"Copper Rockfish": {
"common": "Copper Rockfish",
"scientific": "Sebastes caurinus",
"info": "A relatively common rockfish of the Eastern Pacific, this widespread species can be found from Alaska to Mexico. They are long-lived fish, with the oldest known individual living to 55 years old.",
"type": "Fish",
"order": "Perciformes",
"family": "Scorpaenidae",
"genus": "Sebastes",
"image_url": "https://images.reeflifesurvey.com/0/species_08_59db3e38ba0e4.w1000.h666.jpg",
"region": "North America",
"holdings": {
"North America": "6 - New York Aquarium ",
"Asia": 0,
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
},
"institutions": {
    "New York Aquarium": "6"

    }
    },

"Swell Shark": {
"common": "Swell Shark",
"scientific": "Cephaloscyllium ventriosum",
"info": "A catshark native to the Eastern Pacific, the swell shark is known for its unique defensive mechanism. When captured by a predator, it jams itself into a rock crevice and sucks in water, making it difficult for the predator to extract.",
"type": "Fish",
"order": "Carcharhiniformes",
"family": "Scyliorhinidae",
"genus": "Cephaloscyllium",
"image_url": "https://www.sharksandrays.com/wp-content/uploads/2020/11/California-Swellshark-020.jpg",
"region": "North America, South America",
"holdings": {
"North America": "1.1 - New York Aquarium ",
"Asia": 0,
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
    },
"institutions": {
"New York Aquarium": "1.1"

        }
        },

"Ocellaris Clownfish": {
"common": "Ocellaris Clownfish",
"scientific": "Amphiprion ocellaris",
"info": "One of the most famous of all fish, the ocellaris clownfish is hardy, brightly colored species that is very popular in aquariums, both private and public. Like all clownfish it associates with anemones for protection, and it typically can be found in the magnificent sea anemone and 2 species of carpet anemones.",
"type": "Fish",
"order": "Blenniiformes",
"family": "Pomacentridae",
"genus": "Amphiprion",
"image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ad/Amphiprion_ocellaris_%28Clown_anemonefish%29_by_Nick_Hobgood.jpg/1280px-Amphiprion_ocellaris_%28Clown_anemonefish%29_by_Nick_Hobgood.jpg",
"region": "Asia, Oceania",
"holdings": {
"North America": "16.16 - New York Aquarium ",
"Asia": 0,
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
    },
"institutions": {
"New York Aquarium": "16.16"

    }
    },

"Giant Pacific Octopus": {
"common": "Giant Pacific Octopus",
"scientific": "Enteroctopus dofleini",
"info": "Sometimes referred to by the acronym GPO, the giant Pacific octopus is the largest species of octopus in the world. Its arm span is 14ft typically, and larger individuals had an arm span of 20ft. They are high-ranking predators, feeding on a variety of fish and crustaceans, and can be found from the intertidal zone to 6,000ft down in the ocean.",
"type": "Invertebrate",
"order": "Octopoda",
"family": "Enteroctopodidae",
"genus": "Enteroctopus",
"image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/357027043/original.jpg",
"region": "North America, Asia",
"holdings": {
"North America": "1.0 - New York Aquarium ",
"Asia": 0,
"Europe": 0,
"Africa": 0,
"South America": 0,
"Oceania": 0,
    },
"institutions": {
"New York Aquarium": "1.0"

        },
    },
    "Pore Coral": {
        "common": "Pore Coral",
        "scientific": "Montipora grisea",
        "info": "This encrusting SPS coral is typically brown or green in color, but also appears in blue or pink shades as well. This species has slightly exsert corallites, with bumps surrounding the corallites known as thecal papillae. It is a common species and lives in upper reef slopes.",
        "type": "Invertebrate",
        "order": "Scleractinia",
        "family": "Acroporidae",
        "genus": "Montipora",
        "images": [
            {"label": "Brown form", "url": "https://www.coralsoftheworld.org/media/images/0257_C04_03.jpg"},
            {"label": "Green form", "url": "https://upload.wikimedia.org/wikipedia/commons/d/d2/Montipora_grisea_2.jpg"},
            {"label": "Pink form", "url": "https://upload.wikimedia.org/wikipedia/commons/6/6c/Montipora_grisea.jpg"},
        ],
        "image_url": "https://example.com/default.jpg",
        "region": "Asia, Africa, Oceania",
        "holdings": {
            "North America": "1 [Brown form] 1 [Green form] 1 [Pink form] - New York Aquarium",
            "Europe": 0,
            "Asia": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "New York Aquarium": "1 [Brown form], 1 [Green form], 1 [Pink form]"

                },
            },
            "Milli Staghorn Coral": {
                "common": "Milli Staghorn Coral",
                "scientific": "Acropora millepora",
                "info": "A common branching SPS coral, the milli staghorn is found from east Africa to the central Pacific, and have a corymbose (dense, irregular) structure. They tend to come in several color variants such as brown, muddy green, blue, or purple.",
                "type": "Invertebrate",
                "order": "Scleractinia",
                "family": "Acroporidae",
                "genus": "Acropora",
                "images": [
                    {"label": "Green form", "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/91/Acropora_millepora_Maldives.jpg/1280px-Acropora_millepora_Maldives.jpg"},
                ],
                "image_url": "https://example.com/default.jpg",
                "region": "Asia, Africa, Oceania",
                "holdings": {
                    "North America": "1 [Green form] - New York Aquarium",
                    "Europe": 0,
                    "Asia": 0,
                    "Africa": 0,
                    "South America": 0,
                    "Oceania": 0,
                },
                "institutions": {
                    "New York Aquarium": "1 [Green form]"
                    
                    },
                    },
    
                "Cherry Shrimp": {
                    "common": "Cherry Shrimp",
                    "scientific": "Neocaridina davidi",
                    "info": "A small freshwater shrimp, the cherry shrimp is extremely popular in the aquarium trade for its relative hardiness and various colorful morphs bred over time by aquarists. The wild type is a translucent brown, and can be found in East and Southeast Asia. They consume biofilm and various scraps of detritus found in their environment.",
                    "type": "Invertebrate",
                    "order": "Decapoda",
                    "family": "Atyidae",
                    "genus": "Neocaridina",
                    "images": [
                        {"label": "Wild type", "url": "https://aquaticarts.com/cdn/shop/products/Wild_Form_Neocaridina_13_1800x1800.jpg?v=1660149172"},
                    ],
                    "image_url": "https://example.com/default.jpg",
                    "region": "Asia",
                    "holdings": {
                        "North America": "100 [Wild type] - New York Aquarium",
                        "Europe": 0,
                        "Asia": 0,
                        "Africa": 0,
                        "South America": 0,
                        "Oceania": 0,
                    },
                    "institutions": {
                        "New York Aquarium": "100 [Wild type]"

                    },
                    },

                    "Bee Shrimp": {
                    "common": "Bee Shrimp",
                    "scientific": "Caridina cantonensis",
                    "info": "Like its cousin the cherry shrimp, the bee shrimp is immensely popular in the home aquarium trade. However, due to its limited distribution and habitat in its native Taiwan, it requires more specialized water conditions. Bee shrimp typically prefer softer and more acidic water with a low pH, otherwise their health will deteriorate.",
                    "type": "Invertebrate",
                    "order": "Decapoda",
                    "family": "Atyidae",
                    "genus": "Caridina",
                    "images": [
                        {"label": "Wild type", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/1981267/original.JPG"},
                    ],
                    "image_url": "https://example.com/default.jpg",
                    "region": "Asia",
                    "holdings": {
                        "North America": "50 [Wild type] - New York Aquarium",
                        "Europe": 0,
                        "Asia": 0,
                        "Africa": 0,
                        "South America": 0,
                        "Oceania": 0,
                    },
                    "institutions": {
                        "New York Aquarium": "50 [Wild type]"

                        },
                        },
                        
                        "Bubble-Tip Anemone": {
                        "common": "Bubble-Tip Anemone",
                        "scientific": "Entacmaea quadricolor",
                        "info": "A common Indo-Pacific anemone, the bubble-tip anemone has a large range and forms symbiotic relationships with 14 clownfish species, a damselfish species, and a commensal shrimp. Like many anemones they are photosynthetic, and appear in a variety of morphs, including rose, orange, pink, and green.",
                        "type": "Invertebrate",
                        "order": "Actiniaria",
                        "family": "Actiniidae",
                        "genus": "Entacmaea",
                            "images": [
                                {"label": "Rose form", "url": "https://vividaquariums.com/cdn/shop/products/6453_660x369.jpeg?v=1647307856"},
                                {"label": "Green form", "url": "https://www.waikikiaquarium.org/wp-content/uploads/2013/11/bulbtip-anemone_620.jpg"},
                                {"label": "Pink form", "url": "https://fantaseaaquariums.com/wp-content/uploads/2021/09/Rose-bubble-tip-anemone.jpg"},
                                {"label": "Orange form", "url": "https://www.sealifebase.se/images/species/Enqua_uk.jpg"},

                            ],
                        "image_url": "https://example.com/default.jpg",
                        "region": "Asia, Africa, Oceania",
                        "holdings": {
                            "North America": "4 [Rose form] 4 [Green form] 4 [Pink form] 4 [Orange form] - New York Aquarium",
                            "Europe": 0,
                            "Asia": 0,
                            "Africa": 0,
                            "South America": 0,
                            "Oceania": 0,
                        },
                        "institutions": {
                            "New York Aquarium": "4 [Rose form], 4 [Green form], 4 [Pink form], 4 [Orange form]"

                            }
                            },
                            
                        "Giant Green Anemone": {
                        "common": "Giant Green Anemone",
                        "scientific": "Anthopleura xanthogrammica",
                        "info": "A large sea anemone native to the Eastern Pacific, the giant green anemone is found in the intertidal zone. Well adapted for its habitat, this species has a powerful foot that allows it to remain anchored while waves crash. The main food source seems to be detached mussels. but it eats a variety of animals, including juvenile seabirds.",
                        "type": "Invertebrate",
                        "order": "Actiniaria",
                        "family": "Actiniidae",
                        "genus": "Anthopleura",
                        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Anthopleura_xanthogrammica_1.jpg/1024px-Anthopleura_xanthogrammica_1.jpg",
                        "region": "North America",
                        "holdings": {
                        "North America": "3 - New York Aquarium ",
                        "Asia": 0,
                        "Europe": 0,
                        "Africa": 0,
                        "South America": 0,
                        "Oceania": 0,
                            },
                        "institutions": {
                        "New York Aquarium": "3"

                            }
                                },

                            "Flower Tube Anemone": {
                            "common": "Flower Tube Anemone",
                            "scientific": "Cerianthus filiformis",
                            "info": "One of many species of tube-dwelling anemones, the flower tube anemone varies in color, making it difficult to identify to species level. They are filter feeders that excrete a mucus layer to surround their bases while they rest in the sand.",
                            "type": "Invertebrate",
                            "order": "Ceriantharia",
                            "family": "Cerianthidae",
                            "genus": "Cerianthus",
                                "images": [
                                    {"label": "White form", "url": "https://reefguide.org/pix/cerianthusfiliformis1.jpg"},
                                    {"label": "Purple form", "url": "https://scuba.spanglers.com/im/f/2018/12/2018-12-16b-a20214.jpg"}
                                ],
                            "region": "Asia, Oceania",
                            "holdings": {
                            "North America": "3 [White form] 3 [Purple form] - New York Aquarium ",
                            "Asia": 0,
                            "Europe": 0,
                            "Africa": 0,
                            "South America": 0,
                            "Oceania": 0,
                                },
                            "institutions": {
                            "New York Aquarium": "3 [White form], 3 [Purple form] - New York Aquarium"
        },
    }
}
SPECIES = species_data

# --- Region helpers (derived from user-maintained `region` field) ------------
_region_normalizer = re.compile(r"[^a-z]+")

def normalize_region_name(s: str) -> str:
    """Lowercase and strip non-letters so 'NorthAmerica', 'north america', 'NORTH-AMERICA' all normalize."""
    return _region_normalizer.sub("", s.lower())

# Map normalized -> canonical region
REGION_CANON: Dict[str, str] = {normalize_region_name(r): r for r in REGION_ORDER}

def _region_list(entry: Dict[str, Any]) -> List[str]:
    """Return the user-maintained region list (canonicalized to the canonical names where possible)."""
    raw = entry.get("region")  # user-editable field
    out: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str):
                continue
            key = normalize_region_name(item)
            if key in REGION_CANON:
                out.append(REGION_CANON[key])
            elif item.strip():
                # Keep unknown strings as-is so you can spot/fix typos later
                out.append(item.strip())
    elif isinstance(raw, str) and raw.strip():
        # allow comma-delimited string if user prefers
        for piece in raw.split(","):
            key = normalize_region_name(piece)
            if key in REGION_CANON:
                out.append(REGION_CANON[key])
            elif piece.strip():
                out.append(piece.strip())
    return out

def ensure_region_field_for_all() -> None:
    """Ensure every species has a blank 'region' field the user can fill."""
    for entry in species_data.values():
        entry.setdefault("region", [])  # user can edit these in code later

# Initialize the region field at import time
ensure_region_field_for_all()

# --- Normalization helpers ---------------------------------------------------
_normalizer = re.compile(r"[^a-z0-9]+")
def norm(s: str) -> str:
    return _normalizer.sub("", s.lower())

def build_index():
    idx: Dict[str, str] = {}
    for common_key, entry in species_data.items():
        idx[norm(common_key)] = common_key
        sci = entry.get("scientific")
        if isinstance(sci, str) and sci.strip():
            idx[norm(sci)] = common_key
    return idx

species_index = build_index()

def resolve_species_key(user_query: str):
    n = norm(user_query)
    if n in species_index:
        return species_index[n]
    candidates = list(species_index.keys())
    close = difflib.get_close_matches(n, candidates, n=1, cutoff=0.75)
    if close:
        return (None, species_index[close[0]])
    return (None, None)

def get_entry_or_message(user_query: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    resolved = resolve_species_key(user_query)
    if isinstance(resolved, tuple):
        _exact, suggestion_key = resolved
        if suggestion_key:
            sug_entry = species_data.get(suggestion_key, {})
            sug_common = sug_entry.get("common", suggestion_key)
            sug_sci = sug_entry.get("scientific", "")
            hint = f" (**{sug_common}** — *{sug_sci}*)" if sug_sci else f" (**{sug_common}**)"
            return None, f"No exact entry for **{user_query}**. Did you mean{hint}?"
        return None, f"No entry for **{user_query}**."
    key = resolved
    return species_data[key], None

# --- Member resolver (mention / ID / name / nickname) -----------------------
from discord.ext import commands as _cmds  # if not already imported with alias

async def _try_resolve_member(ctx, text: str):
    """
    Resolve a member from mention, ID, username, or nickname.
    Returns discord.Member or None.
    """
    if not text:
        return None

    # Fast path: built-in converter (handles mentions, IDs, exact name#discrim)
    conv = _cmds.MemberConverter()
    try:
        m = await conv.convert(ctx, text)
        return m
    except Exception:
        pass

    # Fallback: case-insensitive search by display_name or name (partial match)
    if ctx.guild:
        t = text.lower()
        candidates = []
        for m in ctx.guild.members:
            dn = (m.display_name or "").lower()
            un = (m.name or "").lower()
            if t == dn or t == un:
                return m
            if t in dn or t in un:
                candidates.append(m)
        if candidates:
            # pick the first partial match
            return candidates[0]
    return None

# --- ZIMS parsing + Institution helpers -------------------------------------
_zims_number_re = re.compile(r"\d+")

def zims_to_count(raw: Any) -> int:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if not isinstance(raw, str):
        return 0
    nums = _zims_number_re.findall(raw)
    return sum(int(n) for n in nums) if nums else 0

def build_institution_index() -> Dict[str, str]:
    idx: Dict[str, str] = {}
    for entry in species_data.values():
        inst_map = entry.get("institutions") or {}
        for inst_name in inst_map.keys():
            idx[norm(inst_name)] = inst_name
    return idx

def resolve_institution_name(query: str) -> Tuple[Optional[str], Optional[str]]:
    idx = build_institution_index()
    if not idx:
        return None, None
    qn = norm(query)
    if qn in idx:
        return idx[qn], None
    candidates = list(idx.keys())
    close = difflib.get_close_matches(qn, candidates, n=1, cutoff=0.75)
    if close:
        return None, idx[close[0]]
    return None, None

def get_holdings_for_institution(inst_name: str):
    items = []
    total = 0
    for sp_key, entry in species_data.items():
        inst_map = entry.get("institutions") or {}
        raw = inst_map.get(inst_name)
        if raw is None:
            continue
        count = zims_to_count(raw)
        if count > 0:
            sci = entry.get("scientific", "")
            common = entry.get("common", sp_key)
            items.append((common, sci, raw, count))
            total += count
    items.sort(key=lambda t: t[0].lower())
    return items, total

def format_institution_holdings(inst_name: str):
    items, total = get_holdings_for_institution(inst_name)
    if not items:
        return f"**{inst_name}** has no recorded holdings yet.", 0, 0

    def fmt(raw, count):
        if isinstance(raw, str) and ('.' in raw or not raw.isdigit()):
            return f"{raw} \u2192 {count}"
        return str(count)

    lines = [
        (f"- **{c}** (*{sci}*): {fmt(raw, n)}" if sci else f"- **{c}**: {fmt(raw, n)}")
        for (c, sci, raw, n) in items
    ]
    text_blocks = list_to_chunks(
        lines,
        header_prefix=f"**Holdings for {inst_name}** (Total individuals: {total}; Species: {len(items)})"
    )
    return text_blocks, total, len(items)

# >>> NEW: cataloged species helpers (denominator for progress)
def _catalog_species_for_zoo(zoo_name: str) -> Set[str]:
    """
    Returns the set of canonical species names that are cataloged (have a non-zero
    'institutions[zoo_name]' entry) for the given zoo.
    """
    items, _total = get_holdings_for_institution(zoo_name)
    # items: List[Tuple[common, sci, raw, count]]
    return {common for (common, _sci, _raw, _count) in items}

# --- Category / group utilities ---------------------------------------------
def all_types():
    return sorted({e.get("type") for e in species_data.values() if e.get("type")})

def all_orders():
    return sorted({e.get("order") for e in species_data.values() if e.get("order")})

def match_type_or_order(query: str, field: str):
    qn = norm(query)
    hits = []
    display_value = None
    for name, entry in species_data.items():
        value = entry.get(field)
        if not value:
            continue
        if norm(value) == qn:
            if display_value is None:
                display_value = value
            hits.append(name)
    return display_value, sorted(hits)

def list_to_chunks(lines: List[str], header_prefix: str, per_message_limit: int = 1800):
    chunks, buf = [], ""
    for line in lines:
        add = line + "\n"
        if len(buf) + len(add) > per_message_limit:
            chunks.append(buf)
            buf = ""
        buf += add
    if buf:
        chunks.append(buf)
    out = []
    for i, chunk in enumerate(chunks, start=1):
        header = f"{header_prefix} – Part {i}/{len(chunks)}\n" if len(chunks) > 1 else f"{header_prefix}\n"
        out.append(header + chunk)
    return out

def format_holdings(holdings: Dict[str, Any]) -> str:
    """
    Always render each region as a header line, then bullet items.
    If the value is 0 or empty, show 'Region: 0'.
    Accepted shapes per region value:
      - list[str] -> header + bullets
      - str/int (non-zero/truthy) -> header + one bullet with that text
      - 0 / "0" / "" / None -> 'Region: 0'
    """
    lines: List[str] = []
    for region in REGION_ORDER:
        if region == "Antarctica":
            continue  # hide Antarctica from holdings display

        value = holdings.get(region, None)

        if value is None or value == "":
            lines.append(f"**{region}:** 0")
            continue

        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
            if items:
                lines.append(f"**{region}:**")
                lines.extend([f"• {item}" for item in items])
            else:
                lines.append(f"**{region}:** 0")
        else:
            if (isinstance(value, (int, float)) and int(value) == 0) or (isinstance(value, str) and value.strip() == "0"):
                lines.append(f"**{region}:** 0")
            else:
                lines.append(f"**{region}:**")
                lines.append(f"• {str(value).strip()}")

    return "\n".join(lines) if lines else "_No holdings data provided_"

# >>> CHANGED: add image_index param + optional images pager support <<<
def build_species_embed(entry: Dict[str, Any], image_index: int = 0) -> discord.Embed:
    entry = get_species_with_overrides(entry)  # apply overrides (e.g., breeding)
    title = entry.get("common", "Unknown")
    sci = entry.get("scientific", "Unknown")
    e = discord.Embed(title=title, description=f"*{sci}*", color=discord.Color.blurple())
    for label in ["Type", "Order", "Family", "Genus"]:
        val = entry.get(label.lower())
        if val:
            e.add_field(name=label, value=val, inline=True)

    # Regions (user-maintained)
    region_list = _region_list(entry)
    region_text = ", ".join(region_list) if region_list else "_None set_"
    e.add_field(name="Region(s)", value=region_text, inline=False)

    # NEW: Breeding difficulty
    e.add_field(name="Breeding Difficulty", value=get_breeding_label(entry), inline=True)

    if entry.get("info"):
        e.add_field(name="About", value=entry["info"], inline=False)

    # Holdings by region
    holdings = entry.get("holdings") or {}
    any_listed = False
    for region in REGION_ORDER:
        value = holdings_to_bullets(holdings.get(region))
        if value != "—":
            any_listed = True
            e.add_field(name=region, value=value, inline=False)
    if not any_listed:
        e.add_field(name="Holdings", value="No current reported holdings.", inline=False)

    images = entry.get("images") or []
    if isinstance(images, list) and len(images) > 0:
        idx = max(0, min(image_index, len(images) - 1))
        img = images[idx]
        url = img.get("url")
        if url:
            e.set_image(url=url)
            label = img.get("label", "Variant")
            e.set_footer(text=f"{label} • {idx+1}/{len(images)}")
    else:
        if entry.get("image_url"):
            e.set_image(url=entry["image_url"])

    return e

# >>> NEW: minimal pager view (only shows when species has multiple images) <<<
class SpeciesPager(discord.ui.View):
    def __init__(self, entry: Dict[str, Any], start_index: int = 0, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.entry = entry
        self.index = start_index
        self.images = entry.get("images") or []
        if len(self.images) <= 1:
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True

    async def _refresh(self, interaction: discord.Interaction):
        embed = build_species_embed(self.entry, self.index)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.images:
            return await interaction.response.defer()
        self.index = (self.index - 1) % len(self.images)
        await self._refresh(interaction)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.images:
            return await interaction.response.defer()
        self.index = (self.index + 1) % len(self.images)
        await self._refresh(interaction)


# ==============================  ADDED: ZOO PROGRESS + OWNERSHIP  ==============================
# ---------- Persistence ----------
_ZOO_DATA_PATH = pathlib.Path(__file__).with_name("zoo_progress.json")

def _load_zoo_data() -> dict:
    if _ZOO_DATA_PATH.exists():
        try:
            data = json.loads(_ZOO_DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    # Ensure buckets exist
    data.setdefault("users", {})                # {uid: {"active_zoo":..., "zoos": {zoo: [species...]}}}
    data.setdefault("ownership", {})            # {uid: {"limit": n, "zoos": [names...]}}
    data.setdefault("directory", {})            # zoo directory (name->meta)
    data.setdefault("contracept", {})           # NEW: {uid: {zoo: {species: True}}}
    data.setdefault("breeding_channels", {})    # NEW: {guild_id: channel_id}
    data.setdefault("birth_log", [])            # NEW: rolling birth feed
    data.setdefault("species_overrides", {})    # NEW: per-species overrides (e.g., breeding label)
    return data


def _save_zoo_data(data: dict) -> None:
    _ZOO_DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------- Canonical species name ----------
def _canonical_species_name(user_input: str) -> Optional[str]:
    """
    Resolve to the canonical species key stored in species_data (common-name key).
    Accepts scientific names and close matches (uses your existing resolver).
    """
    resolved = resolve_species_key(user_input)
    if isinstance(resolved, tuple):
        _exact, suggestion_key = resolved
        return suggestion_key
    return resolved

# ---------- User struct ----------
def _ensure_user_struct(data: dict, user_id: int) -> dict:
    users = data.setdefault("users", {})
    u = users.get(str(user_id))
    if not u:
        u = {"active_zoo": None, "zoos": {}}
        users[str(user_id)] = u
    return u

def _get_active_zoo_or_msg(ctx, user_struct: dict):
    zoo = user_struct.get("active_zoo")
    if not zoo:
        return None, f"{ctx.author.mention} you don’t have an active zoo yet. Set one with `;zoo set <name>`"
    return zoo, None

def _percent(numerator: int, denominator: int) -> float:
    return (numerator / denominator * 100.0) if denominator > 0 else 0.0

# ---------- Zoo Directory (canonical list of valid zoos) --------------------

# (Optional) One-time seed list you can customize. If directory is empty,
# we’ll auto-seed from this the first time you call get_all_zoo_names().
ZOO_DIRECTORY_SEED: list[str] = [
    # Fill these with your real names; examples:
    "Credit River Zoo",
    "Cube Zoological Park",
    "High Uintahs Zoo",
    "Jupiter Reptile Zoo",
    "New York Aquarium",
    "Mint Park Zoo",
    "Shropshire Hills Zoo",
    "Sapporo Reptile Center and National Aquarium",
]

_directory_normalizer = re.compile(r"\s+")

def _norm_zoo_key(name: str) -> str:
    """Normalize for keys: lowercased, single spaces, trim."""
    return _directory_normalizer.sub(" ", (name or "").strip().lower())

def _get_directory(data: dict) -> dict:
    """Return the directory (name -> metadata dict)."""
    return data.setdefault("directory", {})

def _seed_directory_if_empty(data: dict) -> None:
    """If directory is empty and seed list exists, seed it."""
    directory = _get_directory(data)
    if directory:
        return
    if not ZOO_DIRECTORY_SEED:
        return
    # Seed with empty metadata
    for name in ZOO_DIRECTORY_SEED:
        key = _norm_zoo_key(name)
        directory[key] = {"name": name, "location": None, "image": None}
    _save_zoo_data(data)

def add_zoo_to_directory(name: str, *, location: str | None = None, image: str | None = None) -> bool:
    """
    Add a zoo to the directory. Returns True if created, False if it already existed.
    """
    if not name or not name.strip():
        raise ValueError("Zoo name is required")
    data = _load_zoo_data()
    directory = _get_directory(data)
    key = _norm_zoo_key(name)
    if key in directory:
        return False
    directory[key] = {"name": name.strip(), "location": location, "image": image}
    _save_zoo_data(data)
    return True

def remove_zoo_from_directory(name: str) -> bool:
    """Remove by name. Returns True if removed, False if not present."""
    data = _load_zoo_data()
    directory = _get_directory(data)
    key = _norm_zoo_key(name)
    if key in directory:
        del directory[key]
        _save_zoo_data(data)
        return True
    return False

def set_zoo_meta(name: str, *, location: str | None = None, image: str | None = None) -> bool:
    """Update location/image for an existing zoo. Returns True if updated."""
    data = _load_zoo_data()
    directory = _get_directory(data)
    key = _norm_zoo_key(name)
    node = directory.get(key)
    if not node:
        return False
    if location is not None:
        node["location"] = location
    if image is not None:
        node["image"] = image
    _save_zoo_data(data)
    return True

def is_valid_zoo_name(name: str) -> bool:
    """Case/space-insensitive check against directory."""
    data = _load_zoo_data()
    _seed_directory_if_empty(data)
    directory = _get_directory(data)
    return _norm_zoo_key(name) in directory

def get_all_zoo_names() -> list[str]:
    """
    Return canonical zoo names from directory (pretty-cased).
    Auto-seeds from ZOO_DIRECTORY_SEED if directory is empty.
    """
    data = _load_zoo_data()
    _seed_directory_if_empty(data)
    directory = _get_directory(data)
    # keep stored pretty names
    names = [entry.get("name") or raw for raw, entry in directory.items()]
    # Sort case-insensitively by display name
    return sorted(names, key=lambda s: (s or "").lower())

# ---------- Simple commands to manage/show the directory --------------------

@bot.command(name="zooadd")
async def zooadd_cmd(ctx, *, name: str):
    """
    ;zooadd <Zoo Name>
    Add a zoo to the canonical directory (no metadata). Anyone can use or restrict with your admin check.
    """
    # If you want admin-only, uncomment:
    # if not _is_admin(ctx):
    #     await ctx.send("🚫 Admins only.")
    #     return

    created = add_zoo_to_directory(name)
    if created:
        await ctx.send(f"✅ Added **{name}** to the zoo directory.")
    else:
        await ctx.send(f"ℹ️ **{name}** is already in the zoo directory.")

@bot.command(name="zoolist", aliases=["zoos"])
async def zoolist_cmd(ctx):
    """
    ;zoolist
    Show all valid zoo names (from the directory).
    """
    names = get_all_zoo_names()
    if not names:
        await ctx.send("No zoos in the directory yet.")
        return
    # Discord likes shorter messages; chunk if needed
    chunk = []
    lines_sent = 0
    for nm in names:
        line = f"• {nm}"
        if sum(len(s) + 1 for s in chunk) + len(line) > 1800:
            await ctx.send("\n".join(chunk))
            chunk = []
            lines_sent += 1
        chunk.append(line)
    if chunk:
        await ctx.send("\n".join(chunk))


# ---------- Ownership ----------
DEFAULT_ZOO_LIMIT = 2  # change this default if you like

def _norm_zoo(name: str) -> str:
    return " ".join(name.strip().split()).lower()

def _get_user_ownership(data: dict, user_id: int) -> dict:
    ownership = data.setdefault("ownership", {})
    rec = ownership.get(str(user_id))
    if not rec:
        rec = {"limit": DEFAULT_ZOO_LIMIT, "zoos": []}
        ownership[str(user_id)] = rec
    # dedupe by normalized name while keeping original case
    seen = set()
    cleaned = []
    for z in rec["zoos"]:
        nz = _norm_zoo(z)
        if nz not in seen:
            seen.add(nz)
            cleaned.append(z)
    rec["zoos"] = cleaned
    return rec

def _owns_zoo(ownership: dict, zoo_name: str) -> bool:
    target = _norm_zoo(zoo_name)
    return any(_norm_zoo(z) == target for z in ownership.get("zoos", []))

def _find_cased_zoo_name(ownership: dict, zoo_name: str) -> Optional[str]:
    target = _norm_zoo(zoo_name)
    for z in ownership.get("zoos", []):
        if _norm_zoo(z) == target:
            return z
    return None

def _is_admin(ctx) -> bool:
    if ctx.guild is None:
        return True  # allow in DMs
    author = ctx.author
    return (author == ctx.guild.owner) or getattr(author.guild_permissions, "manage_guild", False)

# --- Member resolver (mention / ID / name / nickname) -----------------------
from discord.ext import commands as _cmds  # if not already imported

async def _try_resolve_member(ctx, text: str):
    """
    Resolve a member from mention, ID, username, or nickname.
    Returns discord.Member or None.
    """
    if not text:
        return None

    # Built-in converter handles mentions, IDs, and exact 'name#discrim'
    try:
        return await _cmds.MemberConverter().convert(ctx, text)
    except Exception:
        pass

    # Fallback: case-insensitive partial match on display name / username
    if ctx.guild:
        t = text.lower().strip()
        exact = None
        partial = []
        for m in ctx.guild.members:
            dn = (m.display_name or "").lower()
            un = (m.name or "").lower()
            if t == dn or t == un:
                exact = m
                break
            if t in dn or t in un:
                partial.append(m)
        if exact:
            return exact
        if partial:
            return partial[0]
    return None


# >>> NEW: helpers to find owners & metadata and build UI ---------------------
def _find_all_owners(data: dict, zoo_name: str) -> List[int]:
    """Return list of user IDs who own this zoo name."""
    owners: List[int] = []
    target = _norm_zoo(zoo_name)
    for uid, rec in data.get("ownership", {}).items():
        for z in rec.get("zoos", []):
            if _norm_zoo(z) == target:
                owners.append(int(uid))
                break
    return owners

def _get_housed_list_for_owner(data: dict, owner_id: int, zoo_name: str) -> List[str]:
    """Return housed species for a given owner's zoo bucket."""
    user = data.get("users", {}).get(str(owner_id))
    if not user:
        return []
    return list(user.get("zoos", {}).get(zoo_name, []))

def _bar(pct: float, length: int = 20) -> str:
    filled = round(pct / 100 * length)
    return "█" * filled + "—" * (length - filled)

def _get_zoo_meta(data: dict, zoo_name: str) -> dict:
    directory = data.setdefault("directory", {})
    entry = directory.get(zoo_name)
    if not entry:
        entry = {"location": None, "image_url": None}
        directory[zoo_name] = entry
    return entry

def _set_zoo_meta_field(data: dict, zoo_name: str, field: str, value: Optional[str]) -> None:
    entry = _get_zoo_meta(data, zoo_name)
    entry[field] = value

def _build_zoo_embed(ctx: commands.Context, zoo_name: str, data: dict) -> discord.Embed:
    owners = _find_all_owners(data, zoo_name)
    owner_mentions = []
    for uid in owners:
        member = None
        if ctx.guild:
            member = ctx.guild.get_member(uid)
        owner_mentions.append(member.mention if member else f"<@{uid}>")
    owner_text = ", ".join(owner_mentions) if owner_mentions else "_Unassigned_"

    # choose housed list: prefer current user if they own it; else first owner
    housed_list: List[str] = []
    if str(ctx.author.id) in data.get("ownership", {}) and _owns_zoo(_get_user_ownership(data, ctx.author.id), zoo_name):
        housed_list = _get_housed_list_for_owner(data, ctx.author.id, zoo_name)
    elif owners:
        housed_list = _get_housed_list_for_owner(data, owners[0], zoo_name)

    housed_valid = [s for s in housed_list if _canonical_species_name(s)]

    # Progress based on cataloged species
    catalog_set = _catalog_species_for_zoo(zoo_name)
    denom = len(catalog_set)
    num = len([s for s in housed_valid if s in catalog_set])
    pct = _percent(num, denom)

    # >>> CHANGED: build a cataloged holdings preview with ✅ next to housed species
    items, total_inds = get_holdings_for_institution(zoo_name)
    def _fmt(raw, count):
        if isinstance(raw, str) and ('.' in raw or not raw.isdigit()):
            return f"{raw} \u2192 {count}"
        return str(count)

    marked_lines: List[str] = []
    for (common, sci, raw, count) in items:
        mark = "✅ " if common in housed_valid else ""
        if sci:
            marked_lines.append(f"- {mark}**{common}** (*{sci}*): {_fmt(raw, count)}")
        else:
            marked_lines.append(f"- {mark}**{common}**: {_fmt(raw, count)}")

    # Show a compact preview (first 10 lines) and hint to use ;holdings for full list
    if marked_lines:
        preview = "\n".join(marked_lines[:10])
        if len(marked_lines) > 10:
            preview += f"\n… (use `;holdings {zoo_name}` for more)"
        catalog_preview = preview
        sp_count = len(items)
    else:
        catalog_preview = "_No cataloged holdings recorded in species_data._"
        sp_count = 0

    meta = _get_zoo_meta(data, zoo_name)
    location = meta.get("location") or "_Unknown_"
    image_url = meta.get("image_url")

    e = discord.Embed(
        title=zoo_name,
        description=f"**Location:** {location}\n**Owner(s):** {owner_text}",
        color=discord.Color.green()
    )
    e.add_field(
        name="Housed Progress",
        value=f"{num}/{denom} cataloged species housed ({pct:.1f}%)\n`{_bar(pct)}`",
        inline=False
    )
    # >>> CHANGED: single field that shows catalog with checkmarks (no separate checklist field)
    e.add_field(
        name=f"Cataloged Holdings (✅ = housed) • Species: {sp_count}",
        value=catalog_preview,
        inline=False
    )

    if image_url:
        e.set_thumbnail(url=image_url)

    return e

# --- Holdings formatting helpers --------------------------------------------

REGION_ORDER = ["North America", "South America", "Europe", "Asia", "Africa", "Oceania", "Antarctica"]

def _normalize_holding_items(raw) -> list[str]:
    """Turn a region's 'holdings' value into a clean list of holders."""
    if raw is None:
        return []
    if raw == 0:
        return []
    if isinstance(raw, str) and raw.strip() in {"", "0", "0.0"}:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        items = [s.strip() for s in re.split(r"[;,]\s*", str(raw))]

    cleaned = [it for it in items if it and it not in {"0", "0.0", "-"}]
    return cleaned

def holdings_to_bullets(raw) -> str:
    """Return a bullet list string for a region, or '—' if none."""
    items = _normalize_holding_items(raw)
    return "\n".join(f"• {it}" for it in items) if items else "—"

# ---------------- Breeding Config & Helpers ----------------
from discord.ext import tasks
import random
from datetime import time as dtime
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# Probability per label (tweak as you like)
BREEDING_PROB = {
    "Very Easy": 0.60,
    "Easy": 0.40,
    "Average": 0.25,
    "Below Average": 0.12,
    "Difficult": 0.05,
    "Impossible": 0.00,
}
DEFAULT_BREEDING_LABEL = "Average"

def set_breeding_channel_for_guild(guild_id: int, channel_id: int) -> None:
    data = _load_zoo_data()
    data["breeding_channels"][str(guild_id)] = int(channel_id)
    _save_zoo_data(data)

def get_breeding_channel_for_guild(guild_id: int) -> Optional[int]:
    data = _load_zoo_data()
    v = data.get("breeding_channels", {}).get(str(guild_id))
    return int(v) if v is not None else None

def set_contracept(user_id: int, zoo_name: str, species_key: str, on: bool) -> None:
    data = _load_zoo_data()
    u = data["contracept"].setdefault(str(user_id), {})
    z = u.setdefault(zoo_name, {})
    if on:
        z[species_key] = True
    else:
        z.pop(species_key, None)
    _save_zoo_data(data)

def is_contracepted(user_id: int, zoo_name: str, species_key: str) -> bool:
    data = _load_zoo_data()
    return bool(
        data.get("contracept", {}).get(str(user_id), {}).get(zoo_name, {}).get(species_key)
    )

def log_birth(entry: dict) -> None:
    data = _load_zoo_data()
    data["birth_log"].append(entry)
    if len(data["birth_log"]) > 2000:
        data["birth_log"] = data["birth_log"][-2000:]
    _save_zoo_data(data)

def get_species_with_overrides(entry: dict) -> dict:
    """Merge species_overrides (e.g., breeding label) without mutating the base."""
    data = _load_zoo_data()
    name = entry.get("common")
    overrides = data.get("species_overrides", {})
    if name and name in overrides:
        merged = dict(entry)
        merged.update(overrides[name])
        return merged
    return entry

def get_breeding_label(entry: dict) -> str:
    label = (entry or {}).get("breeding") or DEFAULT_BREEDING_LABEL
    return label if label in BREEDING_PROB else DEFAULT_BREEDING_LABEL


# ------------- ;zoo command with ownership -------------
@bot.command(name="zoo")
async def zoo_cmd(ctx, subcommand: str = None, *, rest: str = None):
    """
    Ownership-first flow (no ;zoo set needed)

    User subcommands:
    ;zoo status [zoo]      -> show catalog progress for your zoo (auto-picks if you own exactly one)
    ;zoo myzoos            -> show zoos you OWN + your limit usage
    ;zoo view [name]       -> UI card for provided zoo; if omitted and you own exactly one, shows that
    ;zoo meta [zoo] location <text>  -> set location      (auto-picks if one owned)
    ;zoo meta [zoo] image <url>      -> set thumbnail URL (auto-picks if one owned)

    Admin subcommands:
    ;zoo owner add @user <zoo>
    ;zoo owner remove @user <zoo>
    ;zoo owner limit @user <n>
    ;zoo owner list [@user]

    (Deprecated but kept for compatibility)
    ;zoo set / ;zoo clear -> no longer required; we auto-select your zoo.
    """
    data = _load_zoo_data()
    user = _ensure_user_struct(data, ctx.author.id)
    ownership = _get_user_ownership(data, ctx.author.id)

    def _auto_pick_owned_or_msg(owned_list, provided: str | None, need_ownership: bool = True):
        """Return (zoo_name, err_msg). If provided is given, validate (and ownership if need_ownership)."""
        if provided and provided.strip():
            nz = _norm_zoo(provided.strip())
            # If we require ownership, check against list. If not, allow any known institution name resolution.
            if need_ownership:
                for z in owned_list:
                    if _norm_zoo(z) == nz:
                        return z, None
                return None, f"🚫 You don’t own **{provided.strip()}**."
            # not requiring ownership: just use canonical resolution if available
            exact, suggestion = resolve_institution_name(provided.strip())
            if exact:
                return exact, None
            if suggestion:
                return None, f"No exact entry for **{provided.strip()}**. Did you mean **{suggestion}**?"
            return None, f"No institutions recorded yet or no match for **{provided.strip()}**."
        # no provided zoo: auto pick if one, else prompt
        if len(owned_list) == 0:
            return None, "You don’t own any zoos — ask an admin to assign you as an owner."
        if len(owned_list) > 1:
            return None, "You own multiple zoos. Please specify one (e.g., `;zoo status Mint Park Zoo`)."
        return owned_list[0], None

    if subcommand is None:
        await ctx.send(
            "Usage:\n"
            "`;zoo status [zoo]`, `;zoo myzoos`, `;zoo view [name]`\n"
            "`;zoo meta [zoo] location <text>` • `;zoo meta [zoo] image <url>`\n"
            "**Admin:** `;zoo owner add @user <zoo>`, `;zoo owner remove @user <zoo>`, "
            "`;zoo owner limit @user <n>`, `;zoo owner list [@user]`\n"
            "_Note: `;zoo set/clear` are no longer required._"
        )
        return

    sub = subcommand.lower()

    # --- ownership admin ---
    if sub == "owner":
        if not _is_admin(ctx):
            await ctx.send("🚫 You need **Manage Server** to use owner management.")
            return
        if not rest:
            await ctx.send(
                "Owner admin usage:\n"
                "`;zoo owner add @user <zoo>`\n"
                "`;zoo owner remove @user <zoo>`\n"
                "`;zoo owner limit @user <n>`\n"
                "`;zoo owner list [@user]`"
            )
            return

        parts = rest.split()
        sub2 = parts[0].lower()

        def _extract_member_and_tail():
            if ctx.message.mentions:
                member = ctx.message.mentions[0]
                mention_str = f"<@{member.id}>"
                tail = rest
                for k in ("add", "remove", "limit", "list"):
                    tail = tail.replace(k, "", 1)
                tail = tail.replace(mention_str, "", 1).strip()
                return member, tail
            return None, None

        if sub2 == "add":
            member, tail = _extract_member_and_tail()
            if not member or not tail:
                await ctx.send("Usage: `;zoo owner add @user <zoo>`")
                return
            target_data = _load_zoo_data()
            target_own = _get_user_ownership(target_data, member.id)
            zoo_name = " ".join(tail.split())
            if not _owns_zoo(target_own, zoo_name):
                if len(target_own["zoos"]) >= int(target_own.get("limit", DEFAULT_ZOO_LIMIT)):
                    await ctx.send(f"⚠️ {member.mention} is at their limit (**{target_own['limit']}** zoos). Increase with `;zoo owner limit @user <n>`.")
                    return
                target_own["zoos"].append(zoo_name)
                _save_zoo_data(target_data)
            await ctx.send(f"✅ Granted **{zoo_name}** ownership to {member.mention}.")
            return

        if sub2 == "remove":
            member, tail = _extract_member_and_tail()
            if not member or not tail:
                await ctx.send("Usage: `;zoo owner remove @user <zoo>`")
                return
            target_data = _load_zoo_data()
            target_own = _get_user_ownership(target_data, member.id)
            nz = _norm_zoo(tail)
            before = len(target_own["zoos"])
            target_own["zoos"] = [z for z in target_own["zoos"] if _norm_zoo(z) != nz]
            _save_zoo_data(target_data)
            if len(target_own["zoos"]) < before:
                await ctx.send(f"✅ Removed **{tail}** from {member.mention}'s ownership.")
            else:
                await ctx.send(f"ℹ️ {member.mention} didn’t own **{tail}**.")
            return

        if sub2 == "limit":
            member, tail = _extract_member_and_tail()
            if not member or not tail or not tail.split()[0].isdigit():
                await ctx.send("Usage: `;zoo owner limit @user <n>`")
                return
            n = int(tail.split()[0])
            target_data = _load_zoo_data()
            target_own = _get_user_ownership(target_data, member.id)
            target_own["limit"] = max(0, n)
            _save_zoo_data(target_data)
            await ctx.send(f"✅ Set {member.mention}'s zoo limit to **{n}**.")
            return

        if sub2 == "list":
            member = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
            target_own = _get_user_ownership(data, member.id)
            used = len(target_own["zoos"])
            if used == 0:
                await ctx.send(f"{member.mention} owns no zoos. (Limit: {target_own['limit']})")
            else:
                await ctx.send(
                    f"{member.mention} owns ({used}/{target_own['limit']}):\n- " +
                    "\n- ".join(target_own["zoos"])
                )
            return

        await ctx.send("Unknown owner subcommand. Try: `add`, `remove`, `limit`, or `list`.")
        return

    # --- myzoos (user helper) ---
    if sub == "myzoos":
        used = len(ownership["zoos"])
        if used == 0:
            await ctx.send(f"You own no zoos. (Limit: {ownership['limit']})")
        else:
            await ctx.send(
                f"You own ({used}/{ownership['limit']}):\n- " +
                "\n- ".join(ownership["zoos"])
            )
        return

    # >>> meta tools (location/image) -----------------------------------------
    if sub == "meta":
        if not rest:
            await ctx.send("Usage: `;zoo meta [zoo] location <text>` • `;zoo meta [zoo] image <url>`")
            return

        # Allow either:
        #   "location <value>" (auto-pick owned zoo)
        #   "image <value>"    (auto-pick)
        #   "<zoo> location <value>" or "<zoo> image <value>"
        low = rest.lower()
        split_idx = None
        field = None
        for key in (" location ", " image "):
            i = low.find(key)
            if i != -1:
                split_idx = i
                field = key.strip()
                break

        if field is None:
            # maybe starts with the field
            first_word = rest.split(maxsplit=1)[0].lower()
            if first_word in ("location", "image"):
                field = first_word
                value = rest.split(maxsplit=1)[1] if len(rest.split(maxsplit=1)) > 1 else ""
                zoo_name, err = _auto_pick_owned_or_msg(ownership["zoos"], None, need_ownership=True)
                if err:
                    await ctx.send(err); return
            else:
                await ctx.send("Usage: `;zoo meta [zoo] location <text>` • `;zoo meta [zoo] image <url>`")
                return
        else:
            zoo_part = rest[:split_idx].strip()
            value = rest[split_idx + len(field) + 1:].strip()
            if zoo_part:
                zoo_name, err = _auto_pick_owned_or_msg(ownership["zoos"], zoo_part, need_ownership=True)
                if err:
                    await ctx.send(err); return
            else:
                zoo_name, err = _auto_pick_owned_or_msg(ownership["zoos"], None, need_ownership=True)
                if err:
                    await ctx.send(err); return

        if field not in ("location", "image"):
            await ctx.send("Unknown meta field. Use `location` or `image`.")
            return

        data = _load_zoo_data()
        _set_zoo_meta_field(data, zoo_name, "location" if field == "location" else "image_url", value)
        _save_zoo_data(data)
        await ctx.send(f"✅ Updated **{field}** for **{zoo_name}**.")
        return

    # >>> view UI card ---------------------------------------------------------
    if sub == "view":
        # Allow viewing any institution (doesn't require ownership).
        # If no name provided, auto-pick if user owns exactly one.
        if rest and rest.strip():
            exact, suggestion = resolve_institution_name(rest.strip())
            if not exact and suggestion:
                await ctx.send(f"No exact entry for **{rest.strip()}**. Did you mean **{suggestion}**?")
                return
            if not exact and not suggestion:
                await ctx.send(f"No institutions recorded yet or no match for **{rest.strip()}**.")
                return
            target_zoo = exact
        else:
            target_zoo, err = _auto_pick_owned_or_msg(ownership["zoos"], None, need_ownership=False)
            if err:
                await ctx.send("Please provide a zoo name to view (e.g., `;zoo view Mint Park Zoo`).")
                return
        e = _build_zoo_embed(ctx, target_zoo, data)
        await ctx.send(embed=e)
        return

    # --- status ---------------------------------------------------------------
    if sub == "status":
        # `;zoo status [zoo]`
        zoo_name, err = _auto_pick_owned_or_msg(ownership["zoos"], rest, need_ownership=True)
        if err:
            await ctx.send(err); return
        housed = user["zoos"].get(zoo_name, [])
        housed_valid = [s for s in housed if _canonical_species_name(s)]
        catalog_set = _catalog_species_for_zoo(zoo_name)
        denom = len(catalog_set)
        num = len([s for s in housed_valid if s in catalog_set])
        pct = _percent(num, denom)
        bar_len = 20
        filled = round((pct / 100) * bar_len) if denom else 0
        bar = "█" * filled + "—" * (bar_len - filled)
        housed_preview = ", ".join(housed_valid[:20]) + (" …" if len(housed_valid) > 20 else "")
        await ctx.send(
            f"**{zoo_name}** — {num}/{denom} cataloged species housed ({pct:.1f}%)\n"
            f"`{bar}`\n"
            f"**Housed:** {housed_preview if housed_valid else '_None yet_'}"
        )
        return

    # --- list (data buckets, unchanged) --------------------------------------
    if sub == "list":
        zoos_ = list(user["zoos"].keys())
        if not zoos_:
            await ctx.send("You don’t have any zoo data yet. Use `;house <species> at <Your Zoo>` to create one.")
            return
        await ctx.send("Your zoo data buckets:\n- " + "\n- ".join(zoos_))
        return

    # --- deprecated set/clear -------------------------------------------------
    if sub == "set":
        await ctx.send("ℹ️ `;zoo set` is no longer required. Actions auto-select your owned zoo. If you own multiple, specify it (e.g., `;house Whale Shark at Mint Park Zoo`).")
        return

    if sub == "clear":
        await ctx.send("ℹ️ `;zoo clear` is no longer needed. There is no active zoo anymore.")
        return

    await ctx.send("Unknown subcommand. Try `;zoo status [zoo]`, `;zoo myzoos`, `;zoo view [name]`, `;zoo meta [zoo] location …`, or admin `;zoo owner …`.")

# ------------- ;house / ;unhouse -------------
def _parse_house_args(arg: str):
    """
    Supports:
      "<species>"                      (no zoo specified)
      "<species> at <zoo>"
      "<zoo> :: <species>"
    Returns (zoo or None, species or None).
    """
    if not arg:
        return None, None
    s = arg.strip()
    # "<zoo> :: <species>"
    if "::" in s:
        left, right = s.split("::", 1)
        return left.strip(), right.strip()
    # "<species> at <zoo>"  (use last ' at ' to be resilient to species with 'at')
    low = s.lower()
    idx = low.rfind(" at ")
    if idx != -1:
        species = s[:idx].strip()
        zoo = s[idx + 4 :].strip()
        if species and zoo:
            return zoo, species
    return None, s

@bot.command(name="house")
async def house_cmd(ctx, *, species_name: str = None):
    """
    Add a species to your owned zoo’s housed list (ONLY if your zoo actually holds it).
    Usage:
      ;house Whale Shark
      ;house Whale Shark at Mint Park Zoo
      ;house Mint Park Zoo :: Whale Shark
    """
    if not species_name:
        await ctx.send("Usage: `;house <species>` or `;house <species> at <zoo>`")
        return

    data = _load_zoo_data()
    user = _ensure_user_struct(data, ctx.author.id)
    ownership = _get_user_ownership(data, ctx.author.id)

    provided_zoo, sp_part = _parse_house_args(species_name)
    # pick/validate zoo
    if provided_zoo:
        zoo, err = (provided_zoo, None)
        # must own it:
        if not _owns_zoo(ownership, zoo):
            await ctx.send(f"🚫 You don’t own **{provided_zoo}**.")
            return
        # normalize to owned casing if possible
        cased = _find_cased_zoo_name(ownership, zoo) or provided_zoo
        zoo = cased
    else:
        zoo, err = (None, None)
        if len(ownership["zoos"]) == 0:
            await ctx.send("You don’t own any zoos — ask an admin to assign you as an owner.")
            return
        if len(ownership["zoos"]) > 1:
            await ctx.send("You own multiple zoos. Please specify one: `;house <species> at <zoo>` or `;house <zoo> :: <species>`.")
            return
        zoo = ownership["zoos"][0]

    canonical = _canonical_species_name(sp_part)
    if not canonical:
        await ctx.send(f"❌ I don’t recognize **{sp_part}**. Make sure it’s in the catalog.")
        return

    catalog_set = _catalog_species_for_zoo(zoo)
    if canonical not in catalog_set:
        await ctx.send(
            f"🚫 **{canonical}** isn’t in **{zoo}**’s holdings, so it can’t be housed. "
            f"See what you hold with `;holdings {zoo}`."
        )
        return

    housed = user["zoos"].setdefault(zoo, [])
    if canonical in housed:
        await ctx.send(f"ℹ️ **{canonical}** is already housed at **{zoo}**.")
    else:
        housed.append(canonical)
        _save_zoo_data(data)
        denom = len(catalog_set)
        num = len([s for s in housed if _canonical_species_name(s) and s in catalog_set])
        pct = _percent(num, denom)
        await ctx.send(f"✅ Added **{canonical}** to **{zoo}**. Progress: {num}/{denom} ({pct:.1f}%)")

@bot.command(name="unhouse")
async def unhouse_cmd(ctx, *, species_name: str = None):
    """
    Remove a species from your owned zoo’s housed list.
    Usage:
      ;unhouse Whale Shark
      ;unhouse Whale Shark at Mint Park Zoo
      ;unhouse Mint Park Zoo :: Whale Shark
    """
    if not species_name:
        await ctx.send("Usage: `;unhouse <species>` or `;unhouse <species> at <zoo>`")
        return

    data = _load_zoo_data()
    user = _ensure_user_struct(data, ctx.author.id)
    ownership = _get_user_ownership(data, ctx.author.id)

    provided_zoo, sp_part = _parse_house_args(species_name)
    if provided_zoo:
        if not _owns_zoo(ownership, provided_zoo):
            await ctx.send(f"🚫 You don’t own **{provided_zoo}**.")
            return
        zoo = _find_cased_zoo_name(ownership, provided_zoo) or provided_zoo
    else:
        if len(ownership["zoos"]) == 0:
            await ctx.send("You don’t own any zoos — ask an admin to assign you as an owner.")
            return
        if len(ownership["zoos"]) > 1:
            await ctx.send("You own multiple zoos. Please specify one: `;unhouse <species> at <zoo>` or `;unhouse <zoo> :: <species>`.")
            return
        zoo = ownership["zoos"][0]

    canonical = _canonical_species_name(sp_part)
    if not canonical:
        await ctx.send(f"❌ I don’t recognize **{sp_part}**.")
        return

    housed = user["zoos"].setdefault(zoo, [])
    if canonical in housed:
        housed.remove(canonical)
        _save_zoo_data(data)
        catalog_set = _catalog_species_for_zoo(zoo)
        denom = len(catalog_set)
        num = len([s for s in housed if _canonical_species_name(s) and s in catalog_set])
        pct = _percent(num, denom)
        await ctx.send(f"✅ Removed **{canonical}** from **{zoo}**. Progress: {num}/{denom} ({pct:.1f}%)")
    else:
        await ctx.send(f"ℹ️ **{canonical}** isn’t currently housed at **{zoo}**.")

# ============================  END ADDED: ZOO/OWNERSHIP  ============================


# ============================  END ADDED: ZOO/OWNERSHIP  ============================

# --- Commands ----------------------------------------------------------------
@bot.command(name="species", aliases=["card"])
async def cmd_card(ctx: commands.Context, *, name: Optional[str] = None):
    """
    Render a rich embed UI card for a species with image, taxonomy, description,
    and holdings by region.
    """
    try:
        if not name or not str(name).strip():
            await ctx.send("Usage: `;species <name>` — e.g., `;species Whale Shark`")
            return
        
        entry, msg = get_entry_or_message(name)
        if msg:
            await ctx.send(msg)
            return
        
        images = entry.get("images") or []
        embed = build_species_embed(entry, image_index=0)
        
        if isinstance(images, list) and len(images) > 1:
            view = SpeciesPager(entry=entry, start_index=0)
            await ctx.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed)
        
    except Exception:
        log.exception("Error in ;species")
        await ctx.send(f"Sorry, something went wrong building the card for **{name or 'that species'}**.")


@bot.command(name="holdings")
async def cmd_holdings(ctx: commands.Context, *, institution: str):
    """
    Show all species and counts recorded for a specific zoo/aquarium.
    """
    try:
        exact, suggestion = resolve_institution_name(institution)
        if not exact and suggestion:
            await ctx.send(f"No exact entry for **{institution}**. Did you mean **{suggestion}**?")
            return
        if not exact and not suggestion:
            await ctx.send(f"No institutions recorded yet or no match for **{institution}**.")
            return
        
        blocks, total, sp_count = format_institution_holdings(exact)
        if isinstance(blocks, str):
            await ctx.send(blocks)
        else:
            for b in blocks:
                await ctx.send(b)
    except Exception:
        log.exception("Error in ;holdings")
        await ctx.send(f"Sorry, something went wrong looking up holdings for **{institution}**.")


@bot.command(name="type")
async def cmd_type(ctx: commands.Context, *, name: str):
    try:
        # Special case: list ALL species in the DB, send as a .txt file
        if name and name.strip().lower() == "all":
            entries = builtins.sorted(species_data.items(), key=lambda kv: kv[0].lower())
            if not entries:
                await ctx.send("No species are stored yet.")
                return

            lines = []
            for common, entry in entries:
                sci = entry.get("scientific")
                lines.append(f"{common} — {sci}" if (isinstance(sci, str) and sci.strip()) else f"{common}")

            content = f"All Species in Database ({len(lines)} total)\n\n" + "\n".join(lines)
            buf = io.BytesIO(content.encode("utf-8"))
            buf.seek(0)
            file = discord.File(buf, filename="species_all.txt")
            await ctx.send("Here’s a text file with all species:", file=file)
            return

        # Normal behavior (single species or a type category)
        entry, msg = get_entry_or_message(name)
        if not msg:
            value = entry.get("type")
            if value:
                await ctx.send(f"**{entry['common']}** is a **{value}**.")
            else:
                await ctx.send(f"No type information stored for **{entry['common']}**.")
            return

        # If not a species, try interpreting the input as a type name
        display, species_names = match_type_or_order(name, field="type")
        if display and species_names:
            lines = [f"- {n}" for n in species_names]
            for chunk in list_to_chunks(lines, header_prefix=f"**Species in type {display}**"):
                await ctx.send(chunk)
            return

        await ctx.send(msg)
    except Exception:
        log.exception("Error in ;type")
        await ctx.send(f"Sorry, something went wrong processing **{name}**.")




@bot.command(name="order")
async def cmd_order(ctx: commands.Context, *, name: str):
    try:
        entry, msg = get_entry_or_message(name)
        if not msg:
            value = entry.get("order")
            if value:
                await ctx.send(f"**{entry['common']}** belongs to the order **{value}**.")
            else:
                await ctx.send(f"No order information stored for **{entry['common']}**.")
            return
        display, species_names = match_type_or_order(name, field="order")
        if display and species_names:
            lines = [f"- {n}" for n in species_names]
            for chunk in list_to_chunks(lines, header_prefix=f"**Species in order {display}**"):
                await ctx.send(chunk)
            return
        await ctx.send(msg)
    except Exception:
        log.exception("Error in ;order")
        await ctx.send(f"Sorry, something went wrong processing **{name}**.")

@bot.command(name="family")
async def cmd_family(ctx: commands.Context, *, name: str):
    try:
        entry, msg = get_entry_or_message(name)
        if not msg:
            value = entry.get("family")
            if value:
                await ctx.send(f"**{entry['common']}** belongs to the family **{value}**.")
            else:
                await ctx.send(f"No family information stored for **{entry['common']}**.")
            return
        display, species_names = match_type_or_order(name, field="family")
        if display and species_names:
            lines = [f"- {n}" for n in species_names]
            for chunk in list_to_chunks(lines, header_prefix=f"**Species in family {display}**"):
                await ctx.send(chunk)
            return
        await ctx.send(msg)
    except Exception:
        log.exception("Error in ;family")
        await ctx.send(f"Sorry, something went wrong processing **{name}**.")

@bot.command(name="genus")
async def cmd_genus(ctx: commands.Context, *, name: str):
    try:
        entry, msg = get_entry_or_message(name)
        if not msg:
            value = entry.get("genus")
            if value:
                await ctx.send(f"**{entry['common']}** belongs to the genus **{value}**.")
            else:
                await ctx.send(f"No genus information stored for **{entry['common']}**.")
            return
        display, species_names = match_type_or_order(name, field="genus")
        if display and species_names:
            lines = [f"- {n}" for n in species_names]
            for chunk in list_to_chunks(lines, header_prefix=f"**Species in genus {display}**"):
                await ctx.send(chunk)
            return
        await ctx.send(msg)
    except Exception:
        log.exception("Error in ;genus")
        await ctx.send(f"Sorry, something went wrong processing **{name}**.")

@bot.command(name="types")
async def cmd_types(ctx: commands.Context):
    try:
        types = all_types()
        if not types:
            await ctx.send("No type categories are stored yet.")
            return
        lines = [f"- {t}" for t in types]
        for chunk in list_to_chunks(lines, header_prefix=f"**Available Types ({len(types)} total)**"):
            await ctx.send(chunk)
    except Exception:
        log.exception("Error in ;types")
        await ctx.send("Sorry, I couldn't list types right now.")

@bot.command(name="orders")
async def cmd_orders(ctx: commands.Context):
    try:
        orders = all_orders()
        if not orders:
            await ctx.send("No orders are stored yet.")
            return
        lines = [f"- {o}" for o in orders]
        for chunk in list_to_chunks(lines, header_prefix=f"**Available Orders ({len(orders)} total)**"):
            await ctx.send(chunk)
    except Exception:
        log.exception("Error in ;orders")
        await ctx.send("Sorry, I couldn't list orders right now.")

@bot.command(name="specieslist")
async def cmd_specieslist(ctx: commands.Context):
    try:
        names = sorted(species_data.keys(), key=lambda s: s.lower())
        lines = [f"- {n}" for n in names]
        for chunk in list_to_chunks(lines, header_prefix=f"**Species ({len(names)} total)**"):
            await ctx.send(chunk)
    except Exception:
        log.exception("Error in ;specieslist")
        await ctx.send("Sorry, I couldn't list species right now.")

# ---------------- Contraception & Breeding Admin Commands ----------------

@bot.command(name="contracept")
async def contracept_cmd(ctx, *, arg: str = None):
    """
    ;contracept <Species>
    ;contracept <Species> at <Zoo Name>
    ;contracept <Zoo Name> :: <Species>
    Applies contraception for that species in your OWNED zoo.
    """
    if not arg:
        await ctx.send("Usage: `;contracept <Species>` (optionally `at <Zoo Name>` or `<Zoo> :: <Species>`)")
        return

    data = _load_zoo_data()
    ownership = _get_user_ownership(data, ctx.author.id)

    provided_zoo, sp_part = _parse_house_args(arg)
    # Resolve zoo via same rules as ;house
    if provided_zoo:
        if not _owns_zoo(ownership, provided_zoo):
            await ctx.send(f"🚫 You don’t own **{provided_zoo}**.")
            return
        zoo = _find_cased_zoo_name(ownership, provided_zoo) or provided_zoo
    else:
        if len(ownership["zoos"]) == 0:
            await ctx.send("You don’t own any zoos — ask an admin to assign you as an owner.")
            return
        if len(ownership["zoos"]) > 1:
            await ctx.send("You own multiple zoos. Please specify one: `;contracept <Species> at <Zoo>`.")
            return
        zoo = ownership["zoos"][0]

    canonical = _canonical_species_name(sp_part)
    if not canonical:
        await ctx.send(f"❌ I don’t recognize **{sp_part}**.")
        return

    set_contracept(ctx.author.id, zoo, canonical, True)
    await ctx.send(f"🛑 `{canonical}` is now contracepted in **{zoo}**.")

@bot.command(name="uncontracept", aliases=["decontracept"])
async def uncontracept_cmd(ctx, *, arg: str = None):
    """
    ;uncontracept <Species>
    ;uncontracept <Species> at <Zoo Name>
    ;uncontracept <Zoo Name> :: <Species>
    Removes contraception for that species in your OWNED zoo.
    """
    if not arg:
        await ctx.send("Usage: `;uncontracept <Species>` (optionally `at <Zoo Name>` or `<Zoo> :: <Species>`)")
        return

    data = _load_zoo_data()
    ownership = _get_user_ownership(data, ctx.author.id)

    provided_zoo, sp_part = _parse_house_args(arg)
    if provided_zoo:
        if not _owns_zoo(ownership, provided_zoo):
            await ctx.send(f"🚫 You don’t own **{provided_zoo}**.")
            return
        zoo = _find_cased_zoo_name(ownership, provided_zoo) or provided_zoo
    else:
        if len(ownership["zoos"]) == 0:
            await ctx.send("You don’t own any zoos — ask an admin to assign you as an owner.")
            return
        if len(ownership["zoos"]) > 1:
            await ctx.send("You own multiple zoos. Please specify one: `;uncontracept <Species> at <Zoo>`.")
            return
        zoo = ownership["zoos"][0]

    canonical = _canonical_species_name(sp_part)
    if not canonical:
        await ctx.send(f"❌ I don’t recognize **{sp_part}**.")
        return

    set_contracept(ctx.author.id, zoo, canonical, False)
    await ctx.send(f"✅ Contraception removed for `{canonical}` in **{zoo}**.")

@bot.command(name="contraceptstatus", aliases=["breedstatus"])
async def contracept_status_cmd(ctx, *, zoo_name: str = None):
    """
    ;contraceptstatus
    ;contraceptstatus <Zoo Name>
    Lists contracepted species for your OWNED zoo.
    """
    data = _load_zoo_data()
    ownership = _get_user_ownership(data, ctx.author.id)

    if zoo_name:
        if not _owns_zoo(ownership, zoo_name):
            await ctx.send(f"🚫 You don’t own **{zoo_name}**.")
            return
        zoo = _find_cased_zoo_name(ownership, zoo_name) or zoo_name
    else:
        if len(ownership["zoos"]) == 0:
            await ctx.send("You don’t own any zoos — ask an admin to assign you as an owner.")
            return
        if len(ownership["zoos"]) > 1:
            await ctx.send("You own multiple zoos. Use: `;contraceptstatus <Zoo Name>`")
            return
        zoo = ownership["zoos"][0]

    cmap = _load_zoo_data().get("contracept", {}).get(str(ctx.author.id), {}).get(zoo, {})
    if not cmap:
        await ctx.send(f"No species are contracepted in **{zoo}**.")
        return

    names = sorted(cmap.keys(), key=lambda s: s.lower())
    bullet = "\n".join(f"• {n}" for n in names)
    await ctx.send(f"**Contracepted in {zoo}:**\n{bullet}")

@bot.command(name="breedset")
@commands.has_permissions(manage_guild=True)
async def breedset_cmd(ctx, species: str = None, *, label: str = None):
    """
    ;breedset <Species> <Very Easy|Easy|Average|Below Average|Difficult|Impossible>
    Sets the breeding difficulty (global override) for the species card.
    """
    if not species or not label:
        await ctx.send("Usage: `;breedset <Species> <Very Easy|Easy|Average|Below Average|Difficult|Impossible>`")
        return
    label = label.strip().title()
    if label not in BREEDING_PROB:
        await ctx.send(f"Invalid difficulty `{label}`. Valid: {', '.join(BREEDING_PROB.keys())}")
        return

    entry, msg = get_entry_or_message(species)
    if msg:
        await ctx.send(msg); return

    data = _load_zoo_data()
    ov = data.setdefault("species_overrides", {})
    key = entry.get("common") or species
    node = ov.setdefault(key, {})
    node["breeding"] = label
    _save_zoo_data(data)
    await ctx.send(f"✅ Set breeding difficulty for `{key}` → **{label}**.")

@bot.command(name="breedchannel")
@commands.has_permissions(manage_guild=True)
async def breedchannel_cmd(ctx, sub: str = None):
    """
    ;breedchannel set   -> set the current channel for Friday birth announcements
    ;breedchannel show  -> show the current channel
    """
    if sub is None:
        await ctx.send("Usage: `;breedchannel set` or `;breedchannel show`")
        return
    if sub.lower() == "set":
        set_breeding_channel_for_guild(ctx.guild.id, ctx.channel.id)
        await ctx.send(f"✅ Birth announcements will post in {ctx.channel.mention}.")
        return
    if sub.lower() == "show":
        cid = get_breeding_channel_for_guild(ctx.guild.id)
        if cid:
            ch = ctx.guild.get_channel(cid)
            await ctx.send(f"📣 Current birth channel: {ch.mention if ch else f'`{cid}` (not found)'}")
        else:
            await ctx.send("ℹ️ No birth channel set. Use `;breedchannel set` here.")
        return
    await ctx.send("Usage: `;breedchannel set` or `;breedchannel show`")

    
# --- Region helpers ----------------------------------------------------------
_REGION_ALIASES = {
    # canonical: lower-case
    "north america": "North America",
    "na": "North America",
    "n america": "North America",
    "n. america": "North America",
    "usa": "North America",
    "us": "North America",
    "canada": "North America",

    "south america": "South America",
    "sa": "South America",
    "s america": "South America",
    "s. america": "South America",
    "latam": "South America",

    "europe": "Europe",
    "eu": "Europe",

    "asia": "Asia",

    "africa": "Africa",

    "oceania": "Oceania",
    "australia": "Oceania",
    "aus": "Oceania",
    "australasia": "Oceania",
    "nz": "Oceania",
    "new zealand": "Oceania",

    # If you’ve decided to drop Antarctica in UI, you can keep this alias
    # here for backwards-compat, or remove it entirely.
    "antarctica": "Antarctica",
}

_CANONICAL_REGIONS = {
    "North America",
    "South America",
    "Europe",
    "Asia",
    "Africa",
    "Oceania",
    "Antarctica",  # remove if you no longer want it recognized
}

def _canonicalize_region_query(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.strip().lower()
    # direct match
    if t in _REGION_ALIASES:
        return _REGION_ALIASES[t]
    # try to normalize spacing/punctuation
    t2 = t.replace(".", "").replace("_", " ").replace("-", " ").strip()
    if t2 in _REGION_ALIASES:
        return _REGION_ALIASES[t2]
    # title-case direct if already canonical
    tt = text.strip().title()
    if tt in _CANONICAL_REGIONS:
        return tt
    return None

def _extract_species_regions(sp: dict) -> set:
    """
    Returns a set of canonical region strings from the species dict.
    Accepts:
      sp["region"] as:
        - string: "Asia, Europe"
        - list:   ["Asia", "Europe"]
      Also tolerates empty/missing.
    """
    regions = set()
    value = sp.get("region")
    if not value:
        return regions
    # list
    if isinstance(value, (list, tuple, set)):
        candidates = [str(x) for x in value]
    else:
        # string -> split on commas or slashes
        candidates = re.split(r"[,/]", str(value))
    for c in candidates:
        can = _canonicalize_region_query(c)
        if can:
            regions.add(can)
    return regions

def _chunk_lines(lines: list[str], max_chars: int = 1900) -> list[str]:
    """Discord has a 2000 char limit; keep a buffer for code fences/formatting."""
    chunks = []
    buf = ""
    for line in lines:
        if len(buf) + len(line) + 1 > max_chars:
            chunks.append(buf)
            buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        chunks.append(buf)
    return chunks

# --- Command: ;region --------------------------------------------------------
@bot.command(name="region", help="List species by native region. Usage: ;region <region>\nRegions: North America, South America, Europe, Asia, Africa, Oceania")
async def region_command(ctx, *, region: str = ""):
    if not region:
        await ctx.send(
            "Please provide a region: `;region <region>`\n"
            "Options: North America, South America, Europe, Asia, Africa, Oceania"
        )
        return

    wanted = _canonicalize_region_query(region)
    if not wanted:
        await ctx.send(
            f"I didn't recognize `{region}`.\n"
            "Try one of: North America, South America, Europe, Asia, Africa, Oceania"
        )
        return

    # Collect matches. SPECIES is assumed to be your species dict.
    matches = []
    for key, sp in species_data.items():
        try:
            regs = _extract_species_regions(sp)
            if wanted in regs:
                # prefer common name if present, else fall back to dict key
                common = sp.get("common") or key
                sci = sp.get("scientific") or ""
                if sci:
                    matches.append(f"- {common} (*{sci}*)")
                else:
                    matches.append(f"- {common}")
        except Exception:
            # Be resilient to any odd entries
            continue

    matches.sort(key=lambda s: s.lower())
    count = len(matches)

    if count == 0:
        await ctx.send(f"No species marked with native region **{wanted}** yet.")
        return

    # Build neat output with chunking for Discord’s 2000-char limit
    header = f"**Species native to {wanted}** — {count} found"
    lines = [header, ""] + matches
    chunks = _chunk_lines(lines)

    for i, chunk in enumerate(chunks):
        if i == 0:
            await ctx.send(chunk)
        else:
            await ctx.send(chunk)


# --- Error handling ----------------------------------------------------------
@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, CommandNotFound):
        return
    log.exception("Command error: %s", error)
    await ctx.send("An error occurred while processing that command.")

# --- Ready / Run -------------------------------------------------------------
@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    log.info("Bot is ready.")

    # --- Token System (per-user, per-zoo) ---------------------------------------
    TOKENS_FILE = pathlib.Path("tokens.json")
    TOKENS_START = 10
    _GLOBAL_KEY = "__global__"   # fallback bucket for each user

    def _tokens_read(path: pathlib.Path) -> dict:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except Exception:
                    return {}
        return {}

    def _tokens_write(path: pathlib.Path, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _tokens_ensure_structure(data: dict) -> dict:
        """
        Accepts your current shape:
          {"balances": {"334...": 10, "385...": 10}}
        and normalizes to:
          {"balances": {"334...": {"__global__": 10}, "385...": {"__global__": 10}}}
        while preserving any already-per-zoo dicts.
        """
        if not isinstance(data, dict):
            data = {}
        balances = data.get("balances") or {}
        if not isinstance(balances, dict):
            balances = {}
        normalized: dict = {}
        for uid, val in balances.items():
            uid_s = str(uid)
            if isinstance(val, dict):
                normalized[uid_s] = val
            elif isinstance(val, int):
                normalized[uid_s] = {_GLOBAL_KEY: int(val)}
            else:
                normalized[uid_s] = {_GLOBAL_KEY: TOKENS_START}
        data["balances"] = normalized
        return data

    def _load_tokens() -> dict:
        data = _tokens_read(TOKENS_FILE)
        return _tokens_ensure_structure(data)

    def _save_tokens(data: dict):
        data = _tokens_ensure_structure(data)
        _tokens_write(TOKENS_FILE, data)

    def _get_user_bucket(data: dict, uid: int) -> dict:
        uid_s = str(uid)
        if "balances" not in data or not isinstance(data["balances"], dict):
            data["balances"] = {}
        if uid_s not in data["balances"] or not isinstance(data["balances"][uid_s], dict):
            data["balances"][uid_s] = {_GLOBAL_KEY: TOKENS_START}
        return data["balances"][uid_s]

    # -------- Per-zoo API --------
    def get_user_zoo_tokens(uid: int, zoo: str | None) -> int:
        """
        If zoo is provided, return that zoo's balance.
        If zoo is None, return the user's global/default balance.
        If the zoo doesn't exist yet, fall back to global/default (or TOKENS_START).
        """
        data = _load_tokens()
        u = _get_user_bucket(data, uid)
        if zoo:
            return int(u.get(zoo, u.get(_GLOBAL_KEY, TOKENS_START)))
        return int(u.get(_GLOBAL_KEY, TOKENS_START))

    def set_user_zoo_tokens(uid: int, zoo: str, amount: int):
        data = _load_tokens()
        u = _get_user_bucket(data, uid)
        u[zoo] = max(0, int(amount))
        _save_tokens(data)

    def add_user_zoo_tokens(uid: int, zoo: str, delta: int):
        current = get_user_zoo_tokens(uid, zoo)
        set_user_zoo_tokens(uid, zoo, current + int(delta))

    def spend_user_zoo_tokens(uid: int, zoo: str, amount: int) -> bool:
        """
        Attempt to spend `amount` from (uid, zoo). Return True if sufficient and deducted.
        """
        if amount <= 0:
            return True
        current = get_user_zoo_tokens(uid, zoo)
        if current < amount:
            return False
        set_user_zoo_tokens(uid, zoo, current - amount)
        return True

    def list_user_zoos_with_balances(uid: int) -> list[tuple[str, int]]:
        """Return (zoo, tokens) for all explicit zoos (excludes __global__)."""
        data = _load_tokens()
        u = _get_user_bucket(data, uid)
        out = []
        for k, v in u.items():
            if k == _GLOBAL_KEY:
                continue
            try:
                out.append((k, int(v)))
            except Exception:
                pass
        return sorted(out, key=lambda kv: kv[0].lower())

    # -------- Back-compat shims for your existing helpers --------
    def _ensure_balance(data: dict, uid: int) -> int:
        """Return the user's GLOBAL balance (existing behavior)."""
        u = _get_user_bucket(data, uid)
        return int(u.get(_GLOBAL_KEY, TOKENS_START))

    def _add_balance(data: dict, uid: int, n: int) -> int:
        """Add to GLOBAL balance (existing behavior)."""
        u = _get_user_bucket(data, uid)
        cur = int(u.get(_GLOBAL_KEY, TOKENS_START))
        u[_GLOBAL_KEY] = max(0, cur + int(n))
        _save_tokens(data)
        return int(u[_GLOBAL_KEY])

    def _set_balance(data: dict, uid: int, amount: int) -> int:
        """Set GLOBAL balance (existing behavior)."""
        u = _get_user_bucket(data, uid)
        u[_GLOBAL_KEY] = max(0, int(amount))
        _save_tokens(data)
        return int(u[_GLOBAL_KEY])

    # Remove/disable any other @bot.command(name="tokens") first.

    @bot.command(name="tokens")
    async def tokens_cmd(ctx, *, zoo: str | None = None):
        """
        ;tokens <Zoo Name>
        Shows YOUR token balance for that specific valid zoo (from the directory).
        """
        if not zoo:
            await ctx.send("Usage: `;tokens <Zoo Name>` (example: `;tokens Lowell Lagoon`)\nUse `;zoolist` to see valid names.")
            return

        if not is_valid_zoo_name(zoo):
            valid = get_all_zoo_names()
            await ctx.send("🚫 Invalid zoo name.\n" + ("Valid zoos: " + ", ".join(valid) if valid else "No zoos have been added yet. Use `;zooadd <Name>` to add one."))
            return

        # Use the canonical pretty-cased name for display
        # (normalize to fetch exact display name from directory)
        data = _load_zoo_data()
        directory = _get_directory(data)
        canonical = directory.get(_norm_zoo_key(zoo), {}).get("name", zoo)

        amt = get_user_zoo_tokens(ctx.author.id, canonical)
        await ctx.send(f"💰 Your **{canonical}** tokens: **{amt}**.")


    @bot.command(name="token")
    async def token_admin_cmd(ctx, action: str = None, *, rest: str = None):
        """
        Admin token management (no mention needed).
        Syntax:
          ;token add <user> <n> [zoo name...]
          ;token remove <user> <n> [zoo name...]
          ;token set <user> <n> [zoo name...]

        <user> can be mention, ID, username, or nickname.
        [zoo name] is optional; multi-word is supported.
        If [zoo name] is omitted, modifies the user's GLOBAL (default) pool.
        """
        if not _is_admin(ctx):
            await ctx.send("🚫 You need **Manage Server** to modify tokens.")
            return

        valid = {"add", "remove", "set"}
        if not action or action.lower() not in valid or not rest:
            await ctx.send(
                "Usage:\n"
                "`;token add <user> <n> [zoo name]`\n"
                "`;token remove <user> <n> [zoo name]`\n"
                "`;token set <user> <n> [zoo name]`"
            )
            return

        action = action.lower()

        # Parse: <user text> <amount int> [zoo name...]
        import re
        m = re.search(r"\b-?\d+\b", rest)
        if not m:
            await ctx.send("❗ I couldn't find the amount. Example: `;token add Luke 5 OceanWorld`")
            return

        user_text = rest[:m.start()].strip()
        amount_text = m.group(0)
        zoo_text = rest[m.end():].strip() or None

        member = await _try_resolve_member(ctx, user_text)
        if not member:
            await ctx.send(f"❗ I couldn't find a user matching `{user_text}`.")
            return

        try:
            n = int(amount_text)
        except Exception:
            await ctx.send("Amount must be an integer.")
            return

        # If a zoo name is provided, operate on that specific zoo
        if zoo_text:
            # Optional: validate against your directory; uncomment to require valid zoos.
            # if not is_valid_zoo_name(zoo_text):
            #     valid = get_all_zoo_names()
            #     await ctx.send("🚫 Invalid zoo name.\n" + ("Valid zoos: " + ", ".join(valid) if valid else "No zoos defined yet."))
            #     return

            if action == "add":
                if n <= 0:
                    await ctx.send("Add amount must be a positive integer.")
                    return
                add_user_zoo_tokens(member.id, zoo_text, n)
                new_bal = get_user_zoo_tokens(member.id, zoo_text)
                await ctx.send(f"✅ Added **{n}** to **{member.display_name}** for **{zoo_text}**. New balance: **{new_bal}**.")
                return

            if action == "remove":
                if n <= 0:
                    await ctx.send("Remove amount must be a positive integer.")
                    return
                add_user_zoo_tokens(member.id, zoo_text, -n)
                new_bal = get_user_zoo_tokens(member.id, zoo_text)
                await ctx.send(f"✅ Removed **{n}** from **{member.display_name}** for **{zoo_text}**. New balance: **{new_bal}**.")
                return

            if action == "set":
                if n < 0:
                    await ctx.send("Set amount must be zero or positive.")
                    return
                set_user_zoo_tokens(member.id, zoo_text, n)
                await ctx.send(f"✅ Set **{member.display_name}** — **{zoo_text}** to **{n}**.")
                return

        # No zoo provided -> operate on GLOBAL (back-compat)
        data = _load_tokens()
        if action == "add":
            if n <= 0:
                await ctx.send("Add amount must be a positive integer.")
                return
            new_bal = _add_balance(data, member.id, n)
            await ctx.send(f"✅ Added **{n}** to **{member.display_name}**. New default balance: **{new_bal}**.")
        elif action == "remove":
            if n <= 0:
                await ctx.send("Remove amount must be a positive integer.")
                return
            cur = _ensure_balance(data, member.id)
            new_bal = _set_balance(data, member.id, cur - n)
            removed = cur - new_bal
            await ctx.send(f"✅ Removed **{removed}** from **{member.display_name}**. New default balance: **{new_bal}**.")
        elif action == "set":
            if n < 0:
                await ctx.send("Set amount must be zero or positive.")
                return
            new_bal = _set_balance(data, member.id, n)
            await ctx.send(f"✅ Set **{member.display_name}** default balance to **{new_bal}**.")

if __name__ == "__main__":
    # >>> ADDED: start keep-alive web server before running the bot <<<
    keep_alive()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN not set in environment or .env")
        sys.exit(1)
    bot.run(token)

@bot.command(name="commands", aliases=["help", "h"])
async def help_command(ctx):
    """Displays grouped help for all commands with usage examples."""
    sections = [
        (
            "📚 Species & Taxonomy",
            [
                "**;species <name>** — Show a species card with image, taxonomy, and region holdings.",
                "**;specieslist** — List every species stored (chunked for Discord).",
                "**;type <type>** — List species in a type (e.g., Mammal, Fish).",
                "**;type all** — Get a downloadable text file of *all* species in the database.",
                "**;order <order>** — List species in a taxonomic order (e.g., Carnivora).",
                "**;family <family>** — List species in a family (e.g., Felidae).",
                "**;genus <genus>** — List species in a genus (e.g., Felis).",
                "**;types** — Show all available type categories.",
                "**;orders** — Show all available orders.",
                "**;region <region>** — List species native to a region (North America, South America, Europe, Asia, Africa, Oceania).",
            ],
        ),
        (
            "🏛️ Institutions (Directory & Catalog)",
            [
                "**;holdings <Zoo Name>** — Show all cataloged species and counts recorded for that institution.",
                "**;zooadd <Zoo Name>** — Add a zoo to the canonical directory.",
                "**;zoolist** (alias **;zoos**) — Show all valid zoo names in the directory.",
                "**;zooremove <Zoo Name>** — Remove a zoo from the directory.",
                "**;zoo view [Zoo Name]** — Show the zoo’s UI card (owners, progress bar, and catalog preview).",
                "**;zoo status [Zoo Name]** — Show your housed progress for a zoo you own.",
                "**;zoo myzoos** — List the zoos you own and your ownership limit usage.",
                "**;zoo meta [Zoo] location <text>** — Set the location shown on the zoo card.",
                "**;zoo meta [Zoo] image <url>** — Set the thumbnail image shown on the zoo card.",
                "**;zoo list** — Show your personal data buckets (your stored zoo keys).",
                "_Deprecated:_ **;zoo set / ;zoo clear** — No longer required (ownership flow auto-selects).",
            ],
        ),
        (
            "👑 Ownership Admin (Manage Server required)",
            [
                "**;zoo owner add @user <Zoo Name>** — Grant ownership of a zoo.",
                "**;zoo owner remove @user <Zoo Name>** — Revoke ownership of a zoo.",
                "**;zoo owner limit @user <n>** — Set how many zoos a user may own.",
                "**;zoo owner list [@user]** — Show which zoos a user owns and their limit.",
            ],
        ),
        (
            "🏠 Housing (your ‘housed’ checklist)",
            [
                "**;house <Species>** — Mark a species as housed at your owned zoo (auto-picks if you own one).",
                "**;house <Species> at <Zoo Name>** — Specify the zoo explicitly.",
                "**;house <Zoo Name> :: <Species>** — Alternate syntax when names contain ‘at’.",
                "**;unhouse <…>** — Remove a species from your housed list (same argument patterns).",
                "_Note:_ You can only house species that appear in that zoo’s **;holdings**.",
                "**;contracept <Species> [at <Zoo>]** — Prevent a housed species from breeding."
                "**;uncontracept <Species> [at <Zoo>]** — Remove contraception."
                "**;contraceptstatus [Zoo]** — List contracepted species for your zoo."
                "**;breedset <Species> <Difficulty>** — Admin: set breeding difficulty."
                "**;breedchannel set/show** — Admin: set or show the announcement channel."
                "**;breedrun** — Admin: run a manual breeding roll now."
            ],
        ),
        (
            "🪙 Tokens (per-zoo balances + global back-compat)",
            [
                "**;tokens <Zoo Name>** — Show **your** token balance for that specific zoo.",
                "**;token add <user> <n> [Zoo Name]** — Admin: add tokens (global if no zoo provided).",
                "**;token remove <user> <n> [Zoo Name]** — Admin: remove tokens (global if no zoo provided).",
                "**;token set <user> <n> [Zoo Name]** — Admin: set tokens (global if no zoo provided).",
            ],
        ),
        (
            "💡 Tips",
            [
                "• For multi-word names, just type them normally (e.g., `;holdings Mint Park Zoo`).",
                "• Use **;zoolist** to see valid directory names before **;holdings**, **;tokens**, or **;zoo view**.",
                "• If a name isn’t an exact match, commands often suggest the closest match.",
            ],
        ),
    ]

    # Send in chunks to respect Discord’s 2000-char limit
    header = "**Available Commands**\n"
    blocks = []
    cur = header
    for title, lines in sections:
        block = f"\n__{title}__\n" + "\n".join(f"- {ln}" for ln in lines) + "\n"
        if len(cur) + len(block) > 1900:
            blocks.append(cur)
            cur = block
        else:
            cur += block
    if cur.strip():
        blocks.append(cur)

    for i, b in enumerate(blocks):
        await ctx.send(b)

@bot.command(name="zooremove", aliases=["zdel", "zoodrop"])
async def zooremove_cmd(ctx, *, name: str):
    """
    ;zooremove <Zoo Name>
    Removes a zoo from the directory.
    (Currently open to everyone for testing.)
    """
    try:
        # If you want admin-only later, uncomment:
        # if not _is_admin(ctx):
        #     await ctx.send("🚫 Only admins can remove zoos from the directory.")
        #     return

        if not name or not name.strip():
            await ctx.send("Usage: `;zooremove <Zoo Name>`\nTip: `;zoolist` to see valid names.")
            return

        data = _load_zoo_data()
        _seed_directory_if_empty(data)
        directory = _get_directory(data)

        key = _norm_zoo_key(name)
        if key not in directory:
            # Suggest a close match
            import difflib
            candidates = [entry.get("name") or k for k, entry in directory.items()]
            sug = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
            msg = f"⚠️ Zoo **{name}** was not found."
            if sug:
                msg += f" Did you mean **{sug[0]}**?"
            msg += "\nUse `;zoolist` to see valid names."
            await ctx.send(msg)
            return

        display_name = directory[key].get("name") or name
        del directory[key]
        _save_zoo_data(data)

        await ctx.send(f"🗑️ Removed **{display_name}** from the zoo directory.")

    except Exception:
        log.exception("Error in ;zooremove")
        await ctx.send("❌ Unexpected error removing the zoo. Check the console logs.")


# ---------------- Weekly Breeding Engine ----------------

def _nyc_time(hour: int, minute: int = 0) -> dtime:
    tz = ZoneInfo("America/New_York") if ZoneInfo else None
    return dtime(hour=hour, minute=minute, tzinfo=tz)

def _iter_housed_by_user_and_zoo():
    """
    Yields (user_id:int, zoo_name:str, species_list:List[str]) for all housed species.
    """
    data = _load_zoo_data()
    for uid, urec in data.get("users", {}).items():
        zoos = urec.get("zoos", {})
        for zoo_name, species_list in zoos.items():
            yield int(uid), zoo_name, list(species_list or [])

async def _run_breeding_once() -> dict[int, list[str]]:
    """
    Core roll. Returns {guild_id: [lines...]} but we’ll broadcast to every configured guild.
    Since your data isn’t tied to guilds, we build one global set of lines
    then fan it out to all guilds with a configured channel.
    """
    lines: list[str] = []

    for user_id, zoo_name, species_list in _iter_housed_by_user_and_zoo():
        if not species_list:
            continue
        for sp in species_list:
            entry, msg = get_entry_or_message(sp)
            if msg or not entry:
                continue
            entry = get_species_with_overrides(entry)

            # contracept check
            if is_contracepted(user_id, zoo_name, entry.get("common") or sp):
                continue

            # (Optional) pair check — you can enhance to require sexed pairs later
            label = get_breeding_label(entry)
            prob = BREEDING_PROB.get(label, BREEDING_PROB[DEFAULT_BREEDING_LABEL])
            if prob <= 0:
                continue
            if random.random() <= prob:
                mention = f"<@{user_id}>"
                lines.append(f"🍼 **Birth!** `{entry.get('common', sp)}` at **{zoo_name}** (owner {mention}) — difficulty **{label}**")

    # Build per-guild map: broadcast same list to every guild that set a channel
    by_guild: dict[int, list[str]] = {}
    data = _load_zoo_data()
    for gid_str, cid in data.get("breeding_channels", {}).items():
        gid = int(gid_str)
        by_guild[gid] = list(lines)
    return by_guild

@tasks.loop(time=_nyc_time(15, 0))  # 3:00 PM America/New_York daily; we'll gate to Fridays
async def weekly_breeding_loop():
    now = discord.utils.utcnow()
    if ZoneInfo:
        now_local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York"))
        if now_local.weekday() != 4:  # Friday
            return
    else:
        if now.weekday() != 4:
            return

    by_guild = await _run_breeding_once()
    for gid, lines in by_guild.items():
        if not lines:
            continue
        ch_id = get_breeding_channel_for_guild(gid)
        if not ch_id:
            continue
        guild = bot.get_guild(gid)
        if not guild:
            continue
        ch = guild.get_channel(ch_id)
        if not ch:
            continue

        msg = "\n".join(lines)
        for chunk in [msg[i:i+1800] for i in range(0, len(msg), 1800)]:
            await ch.send(f"**Friday Birth Announcements**\n{chunk}")
        # log
        for ln in lines:
            log_birth({
                "timestamp": str(discord.utils.utcnow()),
                "guild_id": gid,
                "channel_id": ch_id,
                "text": ln,
            })

@weekly_breeding_loop.before_loop
async def _before_weekly_breeding():
    # Wait for the bot to be ready
    await bot.wait_until_ready()

# Start via on_connect so we don't have to modify your existing on_ready contents
@bot.event
async def on_connect():
    if not weekly_breeding_loop.is_running():
        weekly_breeding_loop.start()

@bot.command(name="breedrun")
@commands.has_permissions(manage_guild=True)
async def breedrun_cmd(ctx):
    """Manually trigger a breeding roll (posts to the configured channel for this server)."""
    ch_id = get_breeding_channel_for_guild(ctx.guild.id)
    if not ch_id:
        await ctx.send("ℹ️ No birth channel set. Use `;breedchannel set` in the desired channel first.")
        return

    await ctx.send("Rolling breeding now…")
    by_guild = await _run_breeding_once()
    lines = by_guild.get(ctx.guild.id, [])
    channel = ctx.guild.get_channel(ch_id)
    if not channel:
        await ctx.send("Configured channel not found.")
        return
    if not lines:
        await channel.send("No births this roll.")
        return

    msg = "\n".join(lines)
    for chunk in [msg[i:i+1800] for i in range(0, len(msg), 1800)]:
        await channel.send(f"**Birth Announcements (Manual Run)**\n{chunk}")
        # log
        for ln in lines:
            log_birth({
                "timestamp": str(discord.utils.utcnow()),
                "guild_id": ctx.guild.id,
                "channel_id": channel.id,
                "text": ln,
            })
    await ctx.send("Done.")

@bot.command(name="breeddebug")
async def breeddebug_cmd(ctx):
    """
    Diagnostic for breeding. Shows what the bot sees and why births may not appear.
    Run this in a SERVER channel (not DMs).
    """
    if ctx.guild is None:
        await ctx.send("Run this in a server channel (not in DMs).")
        return

    try:
        data = _load_zoo_data()
        lines = []

        # 1) Channel check
        ch_id = get_breeding_channel_for_guild(ctx.guild.id)
        if ch_id:
            ch = ctx.guild.get_channel(ch_id)
            lines.append(f"📣 Announcement channel: {ch.mention if ch else f'<#{ch_id}>(missing)'}")
        else:
            lines.append("⚠️ No breeding channel set. Use `;breedchannel set` in your target channel.")

        users_node = data.get("users") or {}
        if not users_node:
            lines.append("❗ No users recorded in `zoo_progress.json` yet (no housed data).")
            return await ctx.send("\n".join(lines))

        total_users = total_zoos = total_candidates = 0
        skipped_resolve = skipped_contra = skipped_prob0 = skipped_not_in_catalog = 0
        rolled = hits = 0

        for uid_str, urec in users_node.items():
            user_id = int(uid_str)
            total_users += 1
            zoos = urec.get("zoos") or {}
            if not zoos:
                continue

            for zoo_name, species_list in zoos.items():
                total_zoos += 1
                housed = list(species_list or [])
                catalog = _catalog_species_for_zoo(zoo_name)

                lines.append(f"\n👤 <@{user_id}> — **{zoo_name}**")
                lines.append(f"• Housed: {len(housed)} | Catalog: {len(catalog)}")

                if not housed:
                    lines.append("  ↳ No housed species here.")
                    continue

                for sp in housed:
                    total_candidates += 1

                    entry, msg = get_entry_or_message(sp)
                    if msg or not entry:
                        skipped_resolve += 1
                        lines.append(f"  ✖ Resolve failed for `{sp}` ({msg or 'no entry'})")
                        continue

                    entry = get_species_with_overrides(entry)
                    cname = entry.get("common") or sp

                    if cname not in catalog:
                        skipped_not_in_catalog += 1
                        lines.append(f"  ⚠️ `{cname}` is housed but **not in this zoo’s catalog** → ignored.")
                        continue

                    if is_contracepted(user_id, zoo_name, cname):
                        skipped_contra += 1
                        lines.append(f"  🚫 `{cname}` is contracepted → skipped.")
                        continue

                    label = get_breeding_label(entry)
                    prob = BREEDING_PROB.get(label, BREEDING_PROB[DEFAULT_BREEDING_LABEL])
                    if prob <= 0:
                        skipped_prob0 += 1
                        lines.append(f"  0️⃣ `{cname}` difficulty **{label}** (p=0) → skipped.")
                        continue

                    rolled += 1
                    roll = random.random()
                    ok = roll <= prob
                    if ok:
                        hits += 1
                    lines.append(f"  🎲 `{cname}` diff **{label}** p={prob:.2f} roll={roll:.3f} → {'BIRTH' if ok else 'no'}")

        summary = (
            "\n— Summary —\n"
            f"Users:{total_users} Zoos:{total_zoos} Candidates:{total_candidates}\n"
            f"Rolled:{rolled} Hits:{hits} | Skipped: resolve={skipped_resolve}, "
            f"not_in_catalog={skipped_not_in_catalog}, contracept={skipped_contra}, p0={skipped_prob0}"
        )
        text = "\n".join(lines) + summary

        # Chunk for Discord limits
        for i in range(0, len(text), 1900):
            await ctx.send(text[i:i+1900])

    except Exception as e:
        log.exception("breeddebug failed")
        await ctx.send(f"⚠️ breeddebug crashed: `{type(e).__name__}` — {e}")
