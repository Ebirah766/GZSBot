# bot.py
import os
import sys
import logging
import pathlib
import difflib
import re
import json
import io
import builtins
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Dict, Any, Tuple, Optional, List, Set

import discord
from discord.ext import commands, tasks

_seen_messages = {}

# --- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
_ZOO_DATA_PATH = BASE_DIR / "zoo_progress.json"
LOG_FILE = BASE_DIR / "bot.log"

# --- Logging setup -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

log = logging.getLogger("wotbp-bot")

# --- Optional dotenv ---------------------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore

    ENV_FILE = BASE_DIR / ".env"
    loaded = load_dotenv(dotenv_path=ENV_FILE)

    if loaded:
        log.info("Loaded environment from %s", ENV_FILE)
    else:
        log.warning("No .env file loaded from %s", ENV_FILE)

except Exception as e:
    log.warning("python-dotenv not installed or failed to load .env: %s", e)

# --- Zoo data persistence ----------------------------------------------------

def _load_zoo_data() -> dict:
    if not _ZOO_DATA_PATH.exists():
        log.warning("zoo_progress.json does not exist. Creating new datastore.")
        return {
            "users": {},
            "ownership": {},
            "directory": {},
            "contracept": {},
            "breeding_channels": {},
            "birth_log": [],
        }

    try:
        data = json.loads(_ZOO_DATA_PATH.read_text(encoding="utf-8"))

    except json.JSONDecodeError as e:
        log.error("INVALID JSON in zoo_progress.json: %s", e)

        backup = _ZOO_DATA_PATH.with_suffix(".broken.json")
        _ZOO_DATA_PATH.replace(backup)

        log.error("Broken file moved to: %s", backup)

        return {
            "users": {},
            "ownership": {},
            "directory": {},
            "contracept": {},
            "breeding_channels": {},
            "birth_log": [],
        }

    except Exception as e:
        log.error("Failed to load zoo_progress.json: %s", e)

        return {
            "users": {},
            "ownership": {},
            "directory": {},
            "contracept": {},
            "breeding_channels": {},
            "birth_log": [],
        }

    data.setdefault("users", {})
    data.setdefault("ownership", {})
    data.setdefault("directory", {})
    data.setdefault("contracept", {})
    data.setdefault("breeding_channels", {})
    data.setdefault("birth_log", [])

    return data


def _save_zoo_data(data: dict) -> None:
    tmp_path = _ZOO_DATA_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_path.replace(_ZOO_DATA_PATH)

# --- Discord intents ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# --- Bot ---------------------------------------------------------------------
bot = commands.Bot(command_prefix=";", intents=intents)

log.info("Python exe: %s", sys.executable)
log.info("CWD: %s", os.getcwd())
log.info("DISCORD_TOKEN present? %s", "Yes" if os.getenv("DISCORD_TOKEN") else "No")

# --- Constants ---------------------------------------------------------------
REGION_ORDER = [
    "North America",
    "South America",
    "Europe",
    "Asia",
    "Africa",
    "Oceania",
    "Antarctica",
]

# >>> ADDED: keep_alive import <<<
# --- Intents -----------------------------------------------------------------

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

from utils.image_utils import get_display_image_url

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
        "image_url": "https://i.imgur.com/g9rHqjg.jpeg",
    },
    "Whale Shark": {
        "common": "Whale Shark",
        "scientific": "Rhincodon typus",
        "info": "The largest living fish; a gentle filter-feeding giant.",
        "type": "Fish",
        "order": "Orectolobiformes",
        "family": "Rhincodontidae",
        "genus": "Rhincodon",
        "image_url": "https://i.imgur.com/ZaEwqdZ.jpeg",
        "breeding": "Impossible",
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
        "image_url": "https://i.imgur.com/1dfzlDy.jpeg",
        "breeding": "Difficult",
        "region": "Asia",
        "holdings": {},
    },
    "Rhesus Macaque": {
        "common": "Rhesus Macaque",
        "scientific": "Macaca mulatta",
        "info": "Rhesus macaques are a well-known species of macaque native to Asia, from Afghanistan to China. They are exceptionally well-studied due to them being a common laboratory subject.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Cercopithecidae",
        "genus": "Macaca",
        "image_url": "https://i.imgur.com/P4pzgQH.jpeg",
        "breeding": "Average",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Very Easy",
        "region": "North America, South America, Europe, Asia, Africa, Oceania",
        "holdings": {},
    },
    "Wild Boar": {
        "common": "Wild Boar",
        "scientific": "Sus scrofa",
        "info": "The wild boar is the ancestor of the modern domestic pig. It has a large range across 3 continents and is highly adaptable, typically living in loosely-associated herds.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Suidae",
        "genus": "Sus",
        "images": [
            {"label": "Central European wild boar (scrofa)", "url": "https://i.imgur.com/thWfBKo.jpeg"}
        ],
        "breeding": "Average",
        "region": "Europe, Asia",
        "holdings": {},
    },
    "American Mink": {
        "common": "American Mink",
        "scientific": "Neogale vison",
        "info": "A semi-aquatic mustelid native to much of North America, this species has been introduced outside of its native range and become invasive and destructive in Europe.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Mustelidae",
        "genus": "Neogale",
        "image_url": "https://i.imgur.com/AJJ0T83.jpeg",
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {},
    },
    "Water Buffalo": {
        "common": "Water Buffalo",
        "scientific": "Bubalus bubalis",
        "info": "The water buffalo is the domestic variant of the wild water buffalo. First domesticated in India, it has become a pack animal and source of food throughout the world, most commonly in Asia.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Bovidae",
        "genus": "Bubalus",
        "image_url": "https://i.imgur.com/eFSSCV6.jpeg",
        "breeding": "Easy",
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
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {},
    },
    "Eastern Diamondback Rattlesnake": {
        "common": "Eastern Diamondback Rattlesnake",
        "scientific": "Crotalus adamanteus",
        "info": "The eastern diamondback rattlesnake is the largest rattlesnake species. It is endemic to the southeastern United States, and is one of the heaviest species of venomous snakes.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Crotalus",
        "image_url": "https://i.imgur.com/oooU0ga.jpeg",
        "breeding": "Average",
        "region": "North America",
        "holdings": {},
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
            {"label": "South African cheetah (jubatus)", "url": "https://i.imgur.com/29yxH64.jpeg"},
            {"label": "Variant 2 caption", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/344449680/large.jpeg"},
            {"label": "Variant 3 caption", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/344449680/large.jpeg"},
        ],
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/344449680/large.jpeg",
        "breeding": "Difficult",
        "region": "Asia, Africa",
        "holdings": {},
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
            {"label": "American Mustang", "url": "https://i.imgur.com/Nc93ruQ.jpeg"},
            {"label": "Chincoteague Pony", "url": "https://i.imgur.com/Kvlun8O.jpeg"},
        ],
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/41979415/large.jpg",
        "breeding": "Average",
    },
    "Common Fallow Deer": {
        "common": "Common Fallow Deer",
        "scientific": "Dama dama",
        "info": "A very widespread and common deer species thought to have originated in the Mediterranean region, the common fallow deer is a mainstay in temperate and semi-arid environments across Eurasia.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Cervidae",
        "genus": "Dama",
        "image_url": "https://i.imgur.com/tfKvmV0.jpeg",
        "breeding": "Easy",
        "region": "Europe, Asia",
        "holdings": {},
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
        "breeding": "Average",
        "region": "Europe, Asia, Africa",
        "holdings": {},
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
        "breeding": "Easy",
        "region": "South America",
        "holdings": {
            "Europe": "1.2 - Brussels Zoo",},
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
        "breeding": "Average",
        "region": "Asia",
        "holdings": {},
    },
    "Red-Eyed Crocodile Skink": {
        "common": "Red-Eyed Crocodile Skink",
        "scientific": "Tribolonotus gracilis",
        "info": "A skink that is endemic to New Guinea, the red-eyed crocodile skink has obtained high popularity in the private reptile trade recently. They are rather sensitive if wild caught and captive-bred specimens are hardier.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Egerniidae",
        "genus": "Tribolonotus",
        "image_url": "https://i.imgur.com/bboLxRi.jpeg",
        "breeding": "Below Average",
        "region": "Oceania",
        "holdings": {},
    },
    "European Wildcat": {
        "common": "European Wildcat",
        "scientific": "Felis silvestris",
        "info": "A nocturnal wild felid that can be found from the United Kingdom to Azerbaijan. They are endangered in certain regions due to hybridization with domestic and feral cats.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Felis",
        "image_url": "https://i.imgur.com/VU6xnv0.jpeg",
        "breeding": "Average",
        "region": "Europe, Asia",
        "holdings": {},
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
        "breeding": "Average",
        "region": "North America, Europe, Asia",
        "holdings": {},
    },
    "Asian Small-Clawed Otter": {
        "common": "Asian Small-Clawed Otter",
        "scientific": "Aonyx cinereus",
        "info": "The Asian small-clawed otter is the smallest species of otter. They are exceptionally common in zoos due to a need for captive breeding, as the species is listed as Vulnerable in the wild.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Mustelidae",
        "genus": "Aonyx",
        "image_url": "https://i.imgur.com/swILWB3.jpeg",
        "breeding": "Easy",
        "region": "Asia",
        "holdings": {
            "Europe": "3.3 - Parque Zoologico de Clear Coast",},
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
        "breeding": "Below Average",
        "region": "South America",
        "holdings": {},
    },
    "American Flamingo": {
        "common": "American Flamingo",
        "scientific": "Phoenicopterus ruber",
        "info": "Perhaps the most well-known and iconic flamingo species, the American flamingo can be found in North and South America, as well as the Galapagos Islands. When they feed they turn their beaks upside down and filter feed with their beaks.",
        "type": "Bird",
        "order": "Phoenicopteriformes",
        "family": "Phoenicopteridae",
        "genus": "Phoenicopterus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/258687300/large.jpg",
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {
            "North America": [
                "5.5 - Karl Eberhard's Pine Lake Zoo",
                "5.5 - Riverside Zoo"
            ]
        }
    },
    "Southern Screamer": {
        "common": "Southern Screamer",
        "scientific": "Chauna torquata",
        "info": "The southern screamer has an extremely loud call which lends it w its name. It can be heard from up to 2 miles away. They are generally found in wetlands and feed on various vegetation and seeds.",
        "type": "Bird",
        "order": "Anseriformes",
        "family": "Anhimidae",
        "genus": "Chauna",
        "image_url": "https://www.ecoregistros.org/site/images/dataimages/2018/10/02/289707/chaja-1.jpg",
        "breeding": "Below Average",
        "region": "South America",
        "holdings": {
            "Europe": [
                "1.2 - Parque Zoologico de Clear Coast"
            ]
        }
    },
    "American White Ibis": {
        "common": "American White Ibis",
        "scientific": "Eudocimus albus",
        "info": "The American white ibis can be found in coastal areas of North and South America. They gather in massive colonies during breeding season by the waterside and defend their nesting sites fiercely.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Threskiornithidae",
        "genus": "Eudocimus",
        "image_url": "https://i.imgur.com/4NO3q7H.jpeg",
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {},
    },
    "Roseate Spoonbill": {
        "common": "Roseate Spoonbill",
        "scientific": "Platalea ajaja",
        "info": "The roseate spoonbill, much like the flamingo, feeds on crustaceans in the water column by lapping them up. Their pink feather coloration comes from the astaxanthin in the crustaceans they consume.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Threskiornithidae",
        "genus": "Platalea",
        "image_url": "https://i.imgur.com/6h9LYhE.jpeg",
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {
            "Europe": "8.8 - Parque Zoologico de Clear Coast",},
                                },
    "Linnaeus's Two-Toed Sloth": {
        "common": "Linnaeus's Two-Toed Sloth",
        "scientific": "Choloepus didactylus",
        "info": "Linnaeus's two-toed sloth is the largest extant sloth species. They live in the rainforests of northern South America and are closely related to the extinct giant ground sloths.",
        "type": "Mammal",
        "order": "Pilosa",
        "family": "Choloepodidae",
        "genus": "Choloepus",
        "image_url": "https://i.imgur.com/OBjrP82.jpeg",
        "breeding": "Average",
        "region": "South America",
        "holdings": {
            "North America": "1.1 - Riverside Zoo",},
    },
    "White-Faced Saki": {
        "common": "White-Faced Saki",
        "scientific": "Pithecia pithecia",
        "info": "The white-faced saki is a distinctive species of New World monkey native to a small area of South America. The male possesses the distinctive white face, while the female has uniformly black-silver fur.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Pitheciidae",
        "genus": "Pithecia",
        "image_url": "https://www.marwell.org.uk/wp-content/uploads/2021/07/White-faced-saki-Pithecia-pithecia-Marwell-Zoo.jpg",
        "breeding": "Average",
        "region": "South America",
        "holdings": {},
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
        "breeding": "Easy",
        "region": "South America",
        "holdings": {},
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
        "breeding": "Easy",
        "region": "North America, South America",
        "holdings": {},
    },
    "Red-Rumped Agouti": {
        "common": "Red-Rumped Agouti",
        "scientific": "Dasyprocta leporina",
        "info": "The red-rumped agouti is an abundant rodent native to northeastern South America. They have been known to follow troops of monkeys in search of dropped food, benefitting off their diligent foraging.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Dasyproctidae",
        "genus": "Dasyprocta",
        "image_url": "https://i.imgur.com/OjlO8vT.jpeg",
        "breeding": "Easy",
        "region": "South America",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {},
    },
    "Jaguar": {
        "common": "Jaguar",
        "scientific": "Panthera onca",
        "info": "The jaguar is the third largest of the big cats. One of the world's charismatic megafauna, they can be found in North and South America, and are an apex predator in their range, with strong commands of arboreal, terrestrial, and aquatic habitats.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Felidae",
        "genus": "Panthera",
        "image_url": "https://i.imgur.com/F9YGI2o.jpeg",
        "region": "North America, South America",
        "holdings": {},
        "breeding": "Difficult",
    },
    "White-Nosed Coati": {
        "common": "White-Nosed Coati",
        "scientific": "Nasua narica",
        "info": "The white-nosed coati ranges from the southwestern United States to Colombia. It is a highly adaptable species, able to live in a range of habitats, as well as being able to eat many types of food.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Procyonidae",
        "genus": "Nasua",
        "image_url": "https://static.inaturalist.org/photos/358875225/large.jpg",
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {},
    },
    "Chacoan Peccary": {
        "common": "Chacoan Peccary",
        "scientific": "Catagonus wagneri",
        "info": "The Chacoan peccary is one of three extant species of peccaries. For 4 decades it was thought to be extinct and is one of the most well-known examples of a Lazarus taxa. It is an endangered species due to expansion of ranching in its native range.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Tayassuidae",
        "genus": "Catagonus",
        "image_url": "https://i.imgur.com/rWnP6Uz.jpeg",
        "breeding": "Below Average",
        "region": "South America",
        "holdings": {},
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
            {"label": "Mexican Spider Monkey (vellerosus)", "url": "https://i.imgur.com/DFVKKOi.jpeg"}
        ],
        "breeding": "Below Average",
        "region": "North America, South America",
        "holdings": {},
    },
    "Seba's Short-Tailed Bat": {
        "common": "Seba's Short-Tailed Bat",
        "scientific": "Carollia perspicillata",
        "info": "The Seba's short-tailed bat is a common and widespread bat species that feeds on fruit. It is a generalist and will also consume nectar, pollen, and insects. They have a long lifespan, living up to 10 years.",
        "type": "Mammal",
        "order": "Chiroptera",
        "family": "Phyllostomidae",
        "genus": "Carollia",
        "image_url": "https://www.marylandzoo.org/wp-content/uploads/2017/10/bat_web.jpg",
        "breeding": "Below Average",
        "region": "North America, South America",
        "holdings": {},
    },
    "Nine-Banded Armadillo": {
        "common": "Nine-Banded Armadillo",
        "scientific": "Dasypus novemcinctus",
        "info": "The nine-banded armadillo is the most common and by far most widespread armadillo species. Its range is actively expanding as the species migrates northward, being seen in the central United States more frequently than in the past.",
        "type": "Mammal",
        "order": "Cingulata",
        "family": "Dasypodidae",
        "genus": "Dasypus",
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/88373350/original.jpeg",
        "breeding": "Below Average",
        "region": "North America, South America",
        "holdings": {},
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
        "breeding": "Difficult",
        "region": "South America",
        "holdings": {},
    },
    "Tayra": {
        "common": "Tayra",
        "scientific": "Eira barbara",
        "info": "Tayras are one of the larger mustelid species. Found throughout Central and South America, they are known to cache food such as fruit for later, making them one of the few animals to do so.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Mustelidae",
        "genus": "Eira",
        "image_url": "https://i.imgur.com/RYAnuJN.jpeg",
        "breeding": "Difficult",
        "region": "North America, South America",
        "holdings": {},
    },
    "Raccoon": {
        "common": "Raccoon",
        "scientific": "Procyon lotor",
        "info": "The most iconic procyonid, the raccoon is a nocturnal generalist and scavenger that is adapted to a wide range of habitats, including human-inhabited areas. They have been introduced to Europe and Asia and have become established there.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Procyonidae",
        "genus": "Procyon",
        "image_url": "https://i.imgur.com/OrvCS6z.jpeg",
        "breeding": "Easy",
        "region": "North America",
        "holdings": {
            "North America": [
                "1.1 - Riverside Zoo",
                "1.1 - Tri-State Zoo & Aquarium"
            ]
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
    "breeding": "Easy",
    "region": "North America",
    "holdings": {
        "North America": [
            "1.1 - Karl Eberhard's Pine Lake Zoo",
            "0.2 - Tri-State Zoo & Aquarium"
        ]
},
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
        "breeding": "Average",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Average",
        "region": "North America",
        "holdings": {},
    },
    "Black-Tailed Horned Pit Viper": {
        "common": "Black-Tailed Horned Pit Viper",
        "scientific": "Mixcoatlus melanurus",
        "info": "This endangered pit viper is native only to the mountains of southern Mexico. Its distinctive horns lend it the local common name of 'necazcoatl', literally meaning 'eared-serpent'.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Mixcoatlus",
        "image_url": "https://i.imgur.com/iIXFQhT.jpeg",
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
    },
    "Wagler's Pit Viper": {
        "common": "Wagler's Pit Viper",
        "scientific": "Tropidolaemus wagleri",
        "info": "The Wagler's pit viper is also called the Wagler's temple viper, due to its abundance around the Temple of the Azure Cloud in Malaysia. This temple is also known as the Snake Temple due to this species' abundance in the area.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Tropidolaemus",
        "image_url": "https://i.imgur.com/VPxV7ne.jpeg",
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {},
    },
    "Sri Lankan Pit Viper": {
        "common": "Sri Lankan Pit Viper",
        "scientific": "Craspedocephalus trigonocephalus",
        "info": "These venomous snakes are arboreal and nocturnal, occasionally coming down to the ground to feed on animals such as lizards, frogs, small mammals, and birds. When agitated, this species will shake its tail tip, not unlike a rattlesnake.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Craspedocephalus",
        "breeding": "Below Average",
        "image_url": "https://static.inaturalist.org/photos/352173492/large.jpg",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {},
    },
    "Western Mangrove Cat Snake": {
        "common": "Western Mangrove Cat Snake",
        "scientific": "Boiga melanota",
        "info": "This mildly venomous catsnake can be found in Thailand, Malaysia, Singapore, and Sumatra. Once subsumed under Boiga dendrophila, it was split from that species in 2020.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Colubridae",
        "genus": "Boiga",
        "image_url": "https://i.imgur.com/E2Ueb9u.jpeg",
        "breeding": "Average",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
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
            {"label": "Wild type", "url": "https://static.thainationalparks.com/img/species/2021/06/15/397559/naja-kaouthia-juvenile-w-1500.jpg"},
            {"label": "Leucistic", "url": "https://i.imgur.com/pH6Me5E.jpeg"}
        ],
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
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
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {},
    },
    "Blood Python": {
        "common": "Blood Python",
        "scientific": "Python brongersmai",
        "info": "The blood python is native to the Sumatra and Malay Peninsula. It is best known for its vivid red, orange, and brown coloration, which makes it one of the most striking species in the python family.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Pythonidae",
        "genus": "Python",
        "image_url": "https://i.imgur.com/yFWUrdJ.jpeg",
        "breeding": "Difficult",
        "region": "Asia",
        "holdings": {},
    },
    "Chinese Crocodile Lizard": {
        "common": "Chinese Crocodile Lizard",
        "scientific": "Shinisaurus crocodilurus",
        "info": "This living fossil species of lizard can be found exclusively in southern China and northern Vietnam. They are semi-aquatic, claiming a section of pond or stream for themselves, and feed upon small animals like insects, small fish, and frogs.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Shinisauridae",
        "genus": "Shinisaurus",
        "image_url": "https://i.imgur.com/pv45uR7.jpeg",
        "breeding": "Difficult",
        "region": "Asia",
        "holdings": {},
    },
    "Bighorn Sheep": {
        "common": "Bighorn Sheep",
        "scientific": "Ovis canadensis",
        "info": "The bighorn sheep is an iconic species of sheep found throughout alpine regions of western North America, from Canada to Mexico. Several subspecies exist, each adapted for a specific region and climate.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Bovidae",
        "genus": "Ovis",
        "image_url": "https://i.imgur.com/rIpjHYp.jpeg",
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {},
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
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {},
    },
    "North American Porcupine": {
         "common": "North American Porcupine",
        "scientific": "Erethizon dorsatum",
        "info": "The North American porcupine is a large, arboreal rodent native to North America, from northern Canada to central Mexico. It is the second largest rodent in North America after the North American beaver.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Erethizontidae",
        "genus": "Erethizon",
        "image_url": "https://i.imgur.com/vvUhmo8.jpeg",
        "breeding": "Easy",
        "region": "North America",
        "holdings": {
            "North America": "1.1 - Karl Eberhard's Pine Lake Zoo",},
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
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {},
            },
    "Gray Fox": {
        "common": "Gray Fox",
        "scientific": "Urocyon cinereoargenteus",
        "info": "The gray fox is found in both North and South America in a wide variety of habitats. Adaptable like most foxes, it plays a role as an important mesopredator, keeping rodent populations from getting too high.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Canidae",
        "genus": "Urocyon",
        "image_url": "https://i.imgur.com/vlScaM6.jpeg",
        "breeding": "Below Average",
        "region": "North America, South America",
        "holdings": {},
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
    "breeding": "Below Average",
    "region": "North America, South America",
    "holdings": {
        "North America": [
            "3.0 - Essex County Zoo",
            "1.1 - Karl Eberhard's Pine Lake Zoo"
        ]
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
    "image_url": "https://i.imgur.com/6xX7hnd.jpeg",
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {},
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
        "breeding": "Easy",
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
    "breeding": "Average",
    "region": "North America, Europe, Asia, Africa",
    "holdings": {},
},

"Arapaima": {
"common": "Arapaima",
"scientific": "Arapaima gigas",
"info": "The largest bonytongue fish, the arapaima is an apex predator in the Amazon ecosystem. These massive fish protect their young vigorously until they are a substantial size, and can live up to 20 years.",
"type": "Fish",
"order": "Osteoglossiformes",
"family": "Arapaimidae",
"genus": "Arapaima",
"image_url": "https://i.imgur.com/IDZg0RL.jpeg",
    "breeding": "Impossible",
    "region": "South America",
    "holdings": {},
    },

"Japanese Eagle Ray": {
"common": "Japanese Eagle Ray",
"scientific": "Myliobatis tobijei",
"info": "The Japanese eagle ray is a large species of eagle ray that can grow up to 5 feet in length. It is a demersal species, feeding on benthic animals such as crustaceans, fish, and on occasion, aquatic plants.",
"type": "Fish",
"order": "Myliobatiformes",
"family": "Myliobatidae",
"genus": "Myliobatis",
"image_url": "https://www.marinepia.or.jp/picturebook/public/image/upload/594/main01.jpg",
    "breeding": "Difficult",
    "region": "Asia",
    "holdings": {},
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
    "breeding": "Impossible",
    "region": "Asia, Africa, Oceania",
    "holdings": {},
},

"Silver Arowana": {
"common": "Silver Arowana",
"scientific": "Osteoglossum bicirrhosum",
"info": "The silver arowana is a species of bonytongue fish native to South America. It is well known for its powerful jumping ability, which allows it to jump out of the water to hunt its preferred prey.",
"type": "Fish",
"order": "Osteoglossiformes",
"family": "Osteoglossidae",
"genus": "Osteoglossum",
"image_url": "https://i.imgur.com/RUO7Q16.jpeg",
    "breeding": "Impossible",
    "region": "South America",
    "holdings": {},
},

"Red-Bellied Piranha": {
"common": "Red-Bellied Piranha",
"scientific": "Pygocentrus nattereri",
"info": "This iconic species of freshwater fish is native to South America, and has an unjust reputation for being an aggressive man-eater. In reality, they are typically solitary and only congregate and aggressively feed during the dry season.",
"type": "Fish",
"order": "Characiformes",
"family": "Serrasalmidae",
"genus": "Pygocentrus",
"image_url": "https://i.imgur.com/l0XJYo8.jpeg",
    "breeding": "Impossible",
    "region": "South America",
    "holdings": {},
},

"Axolotl": {
"common": "Axolotl",
"scientific": "Ambystoma mexicanum",
"info": "One of the most famous and beloved salamanders, the axolotl is (or was) endemic to a few lakes in the Mexico City area. It is heavily endangered due to development in its former range, and may be extinct in the wild.",
"type": "Amphibian",
"order": "Urodela",
"family": "Ambystomatidae",
"genus": "Ambystoma",
"image_url": "https://i.imgur.com/iFtftRS.jpeg",
    "breeding": "Below Average",
    "region": "North America",
    "holdings": {},
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
    "breeding": "Average",
    "region": "North America",
    "holdings": {
        "North America": [
            "1.1 - Essex County Zoo",
            "1.0 - Tri-State Zoo & Aquarium"
        ]
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
"image_url": "https://i.imgur.com/utYp736.jpeg",
    "breeding": "Below Average",
    "region": "Asia",
    "holdings": {},
},

"Veiled Chameleon": {
"common": "Veiled Chameleon",
"scientific": "Chamaeleo calyptratus",
"info": "This is one of the most well-known and commonly kept chameleon species. Native to the Arabian Peninsula, this species is born pastel green and without its distinctive casque, which it grows later in life.",
"type": "Reptile",
"order": "Squamata",
"family": "Chamaeleonidae",
"genus": "Chamaeleo",
"image_url": "https://i.imgur.com/0ktOcZj.jpeg",
    "breeding": "Below Average",
    "region": "Asia",
    "holdings": {},
},

"Fly River Turtle": {
"common": "Fly River Turtle",
"scientific": "Carettochelys insculpta",
"info": "Perhaps the most unique of all freshwater turtles, the Fly River turtle also goes by the common name of 'pig-nosed turtle' for its distinctive nose, which it uses to breathe in air when fully submerged.",
"type": "Reptile",
"order": "Testudines",
"family": "Carettochelyidae",
"genus": "Carettochelys",
"image_url": "https://i.imgur.com/v0qUNps.jpeg",
    "breeding": "Difficult",
    "region": "Oceania",
    "holdings": {},
},

"Argentine Black and White Tegu": {
"common": "Argentine Black and White Tegu",
"scientific": "Salvator merianae",
"info": "The largest of the tegus, the Argentine black and white tegu is also the most commonly kept as a pet. They are highly intelligent for lizards, and are similar to monitor lizards in behavior, but are not closely related at all.",
"type": "Reptile",
"order": "Squamata",
"family": "Teiidae",
"genus": "Salvator",
"image_url": "https://i.imgur.com/KMQRpvp.jpeg",
    "breeding": "Average",
    "region": "South America",
    "holdings": {},
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
    "breeding": "Below Average",
    "region": "North America, South America",
    "holdings": {},
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
    "breeding": "Difficult",
    "region": "North America",
    "holdings": {
        "North America": "1.0 - Tri-State Zoo & Aquarium", },
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
        "breeding": "Difficult",
        "region": "Oceania",
        "holdings": {},
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
    "breeding": "Difficult",
    "region": "Asia",
    "holdings": {},
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
    "breeding": "Below Average",
    "region": "Asia",
    "holdings": {},
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
            {"label": "Beira locality", "url": "https://www.seriouslyfish.com/wp-content/uploads/2012/05/Nothobranchius-Rachovii-Beira-F1.jpg"},
        ],
        "breeding": "Average",
        "region": "Africa",
        "holdings": {},
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
        "breeding": "Average",
        "region": "Africa",
        "holdings": {},
    },

    "Common Carp": {
        "common": "Common Carp",
        "scientific": "Cyprinus carpio",
        "info": "An extremely hardy cyprinid, the common carp is famous (or infamous) for its adaptability. A generalist, common carp will eat just about anything, and can survive adverse conditions so well that they are one of the most notorious invasive species globally.",
        "type": "Fish",
        "order": "Cypriniformes",
        "family": "Cyprinidae",
        "genus": "Cyprinus",
        "image_url": "https://i.imgur.com/n2piEb4.jpeg",
        "breeding": "Difficult",
        "region": "Europe, Asia",
        "holdings": {
            "North America": [
                "8 - Chicago City Aquarium"
            ]
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
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {
            "North America": [
                "16 - Chicago City Aquarium"
            ]
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
    "image_url": "https://i.imgur.com/dk1RW9e.jpeg",
        "breeding": "Difficult",
        "region": "Europe, Asia, Africa",
        "holdings": {},
    },

    "Rainbow Trout": {
        "common": "Rainbow Trout",
        "scientific": "Oncorhynchus mykiss",
        "info": "One of the most renowned gamefish in the world, the rainbow trout, like many of its cousins, has been introduced to areas outside of its native range, such as Europe, South America, and New Zealand. They are an intensely studied species with several ecotypes, including the endangered steelhead.",
        "type": "Fish",
        "order": "Salmoniformes",
        "family": "Salmonidae",
        "genus": "Oncorhynchus",
        "images": [
            {
                "label": "Coastal rainbow trout (irideus)",
                "url": "https://i.imgur.com/sZBQZDr.png"
            }
        ],
        "region": "North America, Asia",
        "holdings": {
            "North America": "16 [irideus] - Chicago City Aquarium"
        },
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
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {},
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
    "breeding": "Impossible",
    "region": "North America",
    "holdings": {},
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
    "breeding": "Average",
"region": "North America, South America",
"holdings": {},
        },

"Ocellaris Clownfish": {
"common": "Ocellaris Clownfish",
"scientific": "Amphiprion ocellaris",
"info": "One of the most famous of all fish, the ocellaris clownfish is hardy, brightly colored species that is very popular in aquariums, both private and public. Like all clownfish it associates with anemones for protection, and it typically can be found in the magnificent sea anemone and 2 species of carpet anemones.",
"type": "Fish",
"order": "Blenniiformes",
"family": "Pomacentridae",
"genus": "Amphiprion",
"image_url": "https://i.imgur.com/mBGbLwR.jpeg",
    "breeding": "Easy",
    "region": "Asia, Oceania",
    "holdings": {},
    },

"Giant Pacific Octopus": {
"common": "Giant Pacific Octopus",
"scientific": "Enteroctopus dofleini",
"info": "Sometimes referred to by the acronym GPO, the giant Pacific octopus is the largest species of octopus in the world. Its arm span is 14ft typically, and larger individuals had an arm span of 20ft. They are high-ranking predators, feeding on a variety of fish and crustaceans, and can be found from the intertidal zone to 6,000ft down in the ocean.",
"type": "Invertebrate",
"order": "Octopoda",
"family": "Enteroctopodidae",
"genus": "Enteroctopus",
    "breeding": "Difficult",
    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/357027043/original.jpg",
"region": "North America, Asia",
"holdings": {},
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
            {"label": "Brown form", "url": "https://i.imgur.com/FHd0MA6.jpeg"},
            {"label": "Green form", "url": "https://i.imgur.com/xtGd9hW.jpeg"},
            {"label": "Pink form", "url": "https://i.imgur.com/7DSrobl.jpeg"},
        ],
        "breeding": "Difficult",
        "region": "Asia, Africa, Oceania",
        "holdings": {},
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
                    {"label": "Green form", "url": "https://i.imgur.com/ow38gUB.jpeg"},
                ],
                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/105111643/large.jpg",
                "breeding": "Difficult",
                "region": "Asia, Africa, Oceania",
                "holdings": {},
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
                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/42730173/large.jpeg",
                    "breeding": "Very Easy",
                    "region": "Asia",
                    "holdings": {},
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
                        {"label": "Wild type", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/1981267/original.JPG"}
                    ],
                    "breeding": "Average",
                    "region": "Asia",
                    "holdings": {},
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
            {"label": "Rose form",   "url": "https://vividaquariums.com/cdn/shop/products/6453_660x369.jpeg?v=1647307856"},
            {"label": "Green form",  "url": "https://www.waikikiaquarium.org/wp-content/uploads/2013/11/bulbtip-anemone_620.jpg"},
            {"label": "Pink form",   "url": "https://fantaseaaquariums.com/wp-content/uploads/2021/09/Rose-bubble-tip-anemone.jpg"},
            {"label": "Orange form", "url": "https://i.imgur.com/3LlGwfC.jpeg"}
        ],
        "breeding": "Below Average",
        "region": "Asia, Africa, Oceania",
        "holdings": {},
    },


                    "Giant Green Anemone": {
                    "common": "Giant Green Anemone",
                    "scientific": "Anthopleura xanthogrammica",
                    "info": "A large sea anemone native to the Eastern Pacific, the giant green anemone is found in the intertidal zone. Well adapted for its habitat, this species has a powerful foot that allows it to remain anchored while waves crash. The main food source seems to be detached mussels but it eats a variety of animals, including juvenile seabirds.",
                    "type": "Invertebrate",
                    "order": "Actiniaria",
                    "family": "Actiniidae",
                    "genus": "Anthopleura",
                    "image_url": "https://i.imgur.com/FcFFiSz.jpeg",
                    "breeding": "Impossible",
                    "region": "North America",
                        "holdings": {
                             },
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
                                "breeding": "Impossible",
                                "region": "Asia, Oceania",
                                "holdings": {},
                                },

                                "Red-Tailed Catfish": {
                                "common": "Red-Tailed Catfish",
                                "scientific": "Phractocephalus hemioliopterus",
                                "info": "One of the world's largest catfish, the red-tailed catfish is found exclusively in the Amazon River and its associated rivers. They are territorial predators who feed on a variety of different animals, both aquatic and terrestrial. They have been introduced to several tropical countries outside of their native range.",
                                "type": "Fish",
                                "order": "Siluriformes",
                                "family": "Pimelodidae",
                                "genus": "Phractocephalus",
                                "image_url": "https://i.imgur.com/uDWDJ7S.jpeg",
                                "breeding": "Impossible",
                                "region": "South America",
                                "holdings": {},
                                    },

                                    "White-Blotched River Ray": {
                                    "common": "White-Blotched River Ray",
                                    "scientific": "Potamotrygon leopoldi",
                                    "info": "A freshwater stingray endemic to the Xingu River basin in Brazil, the white-blotched river ray is highly prized in the aquarium trade, where it is often bred for specific traits like size and coloration. In the wild, they live in sandy environments, burying themselves in the sand for protection.",
                                    "type": "Fish",
                                    "order": "Myliobatiformes",
                                    "family": "Potamotrygonidae",
                                    "genus": "Potamotrygon",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/923557/large.jpg",
                                    "breeding": "Difficult",
                                    "region": "South America",
                                    "holdings": {},
                                        },

                                    "Ripsaw Catfish": {
                                    "common": "Ripsaw Catfish",
                                    "scientific": "Oxydoras niger",
                                    "info": "A large catfish native to the greater Amazon basin, the ripsaw catfish has recently been upgraded to Endangered on the IUCN Red List, due to overfishing and the damming of rivers needed for spawning. They have very strong bony body armor which protects them from external threats.",
                                    "type": "Fish",
                                    "order": "Siluriformes",
                                    "family": "Doradidae",
                                    "genus": "Oxydoras",
                                    "image_url": "https://i.imgur.com/UbkNxsL.jpeg",
                                    "breeding": "Impossible",
                                    "region": "South America",
                                    "holdings": {},
                                        },

                                    "Electric Eel": {
                                    "common": "Electric Eel",
                                    "scientific": "Electrophorus electricus",
                                    "info": "The best-known electric eel, the electric eel was once a complex of multiple cryptic species, but it was split into multiple in 2019. This nominal species is restricted to the Guiana Shield, and can deliver an electric shock of up to 480 volts.",
                                    "type": "Fish",
                                    "order": "Gymnotiformes",
                                    "family": "Gymnotidae",
                                    "genus": "Electrophorus",
                                    "image_url": "https://www.monaconatureencyclopedia.com/wp-content/uploads/2020/06/6-Electrophorus-electricus.jpg",
                                    "breeding": "Impossible",
                                    "region": "South America",
                                    "holdings": {},
                                        },

                                        "Tambaqui": {
                                        "common": "Tambaqui",
                                        "scientific": "Colossoma macropomum",
                                        "info": "The tambaqui, also known as the black pacu, is a large freshwater fish native to the Amazon and Orinoco river basins of South America. Their main diet is fruits and seeds, witth their teeth being evolved specifically to crush tough food so it is easier to swallow.",
                                        "type": "Fish",
                                        "order": "Characiformes",
                                        "family": "Serrasalmidae",
                                        "genus": "Colossoma",
                                        "image_url": "https://i.imgur.com/4uqef4U.jpeg",
                                        "breeding": "Impossible",
                                        "region": "South America",
                                        "holdings": {},
                                            },

                                    "Iridescent Shark": {
                                    "common": "Iridescent Shark",
                                    "scientific": "Pangasianodon hypophthalmus",
                                    "info": "A large pelagic catfish found in Southeast Asia, the iridescent shark gets its name from its sharklike appearance and iridescence on its scales. They are often sold in pet stores as juveniles or subadults, but their max size of 4.3ft makes it difficult for anyone but the most dedicated aquarist or public aquarium to hold properly.",
                                    "type": "Fish",
                                    "order": "Siluriformes",
                                    "family": "Pangasiidae",
                                    "genus": "Pangasianodon",
                                    "image_url": "https://i.imgur.com/w5KOswU.jpeg",
                                    "breeding": "Impossible",
                                    "region": "Asia",
                                    "holdings": {},
                                        },

                                    "Dwarf Pufferfish": {
                                    "common": "Dwarf Pufferfish",
                                    "scientific": "Carinotetraodon travancoricus",
                                    "info": "One of the smallest pufferfish species, the dwarf pufferfish, also known as the pea pufferfish, is a vulnerable species that can be found exclusively in coastal swamps and rivers of southwestern India. They feed on invertebrates such as insect larvae and crustaceans, living in large schools that disperse during the rainy season.",
                                    "type": "Fish",
                                    "order": "Tetraodontiformes",
                                    "family": "Tetraodontidae",
                                    "genus": "Carinotetraodon",
                                    "image_url": "https://i.imgur.com/gLme9tR.jpeg",
                                    "breeding": "Difficult",
                                    "region": "Asia",
                                    "holdings": {},
                                        },

                                    "Banded Archerfish": {
                                    "common": "Banded Archerfish",
                                    "scientific": "Toxotes jaculatrix",
                                    "info": "This brackish water archerfish is the most common archerfish species found in captivity. As an archerfish, they possess the unique ability amongst fish to spit water at its preferred prey, such as terrestrial insects, to knock them into the water. Interestingly, this is a learned behavior, as young archerfish must watch adults shoot for prey and try it themselves.",
                                    "type": "Fish",
                                    "order": "Carangiformes",
                                    "family": "Toxotidae",
                                    "genus": "Toxotes",
                                    "image_url": "https://i.imgur.com/xxUw0qz.jpeg",
                                    "breeding": "Impossible",
                                    "region": "Asia, Oceania",
                                    "holdings": {},
                                    },

    "Giant Gourami": {
        "common": "Giant Gourami",
        "scientific": "Osphronemus goramy",
        "info": "The giant gourami is one of the largest gourami species. Native to Southeast Asia, it is a voracious herbivore, feeding on aquatic plants relentlessly. They are popular in aquaria but caution is advised due to their large adult size and aggression towards tankmates.",
        "type": "Fish",
        "order": "Anabantiformes",
        "family": "Osphronemidae",
        "genus": "Osphronemus",
        "image_url": "https://i.imgur.com/8TvkN0g.png",
        "breeding": "Impossible",
        "region": "Asia",
        "holdings": {
            "North America": [
                "1 - Chicago City Aquarium"
            ]
        }
    },

                                    "Siberian Sturgeon": {
                                    "common": "Siberian Sturgeon",
                                    "scientific": "Huso baerii",
                                    "info": "This critically endangered species of sturgeon is native to wide swathes of Siberia. Like many sturgeons it is heavily poached in the wild for its valuable roe, which is used to make caviar, and as a result the species is extensively farmed to fuel the demand for the exotic dish.",
                                    "type": "Fish",
                                    "order": "Acipenseriformes",
                                    "family": "Acipenseridae",
                                    "genus": "Huso",
                                    "image_url": "https://i.imgur.com/5pYbIKa.jpeg",
                                    "breeding": "Difficult",
                                    "region": "Asia",
                                    "holdings": {},
                                        },

                                    "Wels Catfish": {
                                    "common": "Wels Catfish",
                                    "scientific": "Silurus glanis",
                                    "info": "The Wels catfish is one of the largest catfish species in the world. As a riverine apex predator, they have a voracious appetite, and have been known to eat pretty much anything that fits in their mouths, from worms to nutrias and even invasive clams.",
                                    "type": "Fish",
                                    "order": "Siluriformes",
                                    "family": "Siluridae",
                                    "genus": "Silurus",
                                    "image_url": "https://www.monaconatureencyclopedia.com/wp-content/uploads/2018/01/1_silurus_glanis.jpg",
                                    "breeding": "Impossible",
                                    "region": "Europe, Asia",
                                    "holdings": {},
                                        },

                                    "Northern Pike": {
                                    "common": "Northern Pike",
                                    "scientific": "Esox lucius",
                                    "info": "A large pike species, the northern pike is famed in its massive Palearctic range for its large size and aggressive fight when caught by fishermen. They are aggressive predators and feed upon many types of animals, even occasionally swans larger than they are.",
                                    "type": "Fish",
                                    "order": "Salmoniformes",
                                    "family": "Esocidae",
                                    "genus": "Esox",
                                    "image_url": "https://i.imgur.com/nvvLct0.jpeg",
                                    "breeding": "Impossible",
                                    "region": "North America, Europe",
                                    "holdings": {},
                                        },

                                    "Fire Salamander": {
                                    "common": "Fire Salamander",
                                    "scientific": "Salamandra salamandra",
                                    "info": "A common and iconic salamander, the fire salamander gets its common name from an old myth that salamanders were born from fire (as salamanders often fled burning logs used for fires when they were lit up). Despite its large range it is listed as Vulnerable on the IUCN Red List due to its susceptibility to infection by the introduced fungus Batrachochytrium salamandrivorans.",
                                    "type": "Amphibian",
                                    "order": "Urodela",
                                    "family": "Salamandridae",
                                    "genus": "Salamandra",
                                    "images": [
                                        {"label": "Central European fire salamander (salamandra)", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/528552933/original.jpg"},
                                        {"label": "Italian fire salamander (gigliolii)", "url": "https://i.imgur.com/mWS1Ob4.jpeg"},
                                        {"label": "Central Spanish fire salamander (almanzoris)", "url": "https://static.inaturalist.org/photos/207341184/large.jpg"},
                                        ],
                                    "breeding": "Below Average",
                                    "region": "Europe",
                                    "holdings": {},
                                        },

                                    "Eurasian Otter": {
                                    "common": "Eurasian Otter",
                                    "scientific": "Lutra lutra",
                                    "info": "The Eurasian otter is a widespread species of otter found on three continents. It is an important predator of various aquatic creatures such as fish and crayfish, and is under threat due to the introduction of non-native fish species to its native range, which impacts its ability to feed.",
                                    "type": "Mammal",
                                    "order": "Carnivora",
                                    "family": "Mustelidae",
                                    "genus": "Lutra",
                                    "image_url": "https://i.imgur.com/KmwWMsg.jpeg",
                                    "breeding": "Average",
                                    "region": "Europe, Asia, Africa",
                                    "holdings": {},
                                        },

                                    "Fahaka Pufferfish": {
                                    "common": "Fahaka Pufferfish",
                                    "scientific": "Tetraodon lineatus",
                                    "info": "The fahaka pufferfish is a large freshwater pufferfish native to west, north, and east Africa. Feeding primarily on mussels and snails, their sharp beaks are adapted for piercing through the tough shells of these mollusks, and they are typically found in open or vegetated habitats.",
                                    "type": "Fish",
                                    "order": "Tetraodontiformes",
                                    "family": "Tetraodontidae",
                                    "genus": "Tetraodon",
                                    "image_url": "https://i.imgur.com/hKY3DXE.jpeg",
                                    "breeding": "Impossible",
                                    "region": "Africa",
                                    "holdings": {},
                                        },

                                    "West African Lungfish": {
                                    "common": "West African Lungfish",
                                    "scientific": "Protopterus annectens",
                                    "info": "The African lungfish is the archetypal lungfish and the most common in captivity. Native to disjunct sections of Africa, this species is remarkably hardy and is the freshwater fish that can go the longest without food, being able to survive 3 1/2 years without any intake.",
                                    "type": "Fish",
                                    "order": "Ceratodontiformes",
                                    "family": "Protopteridae",
                                    "genus": "Protopterus",
                                    "image_url": "https://i.imgur.com/uvj68nP.jpeg",
                                    "breeding": "Impossible",
                                    "region": "Africa",
                                    "holdings": {},
                                        },

                                    "Ornate Bichir": {
                                    "common": "Ornate Bichir",
                                    "scientific": "Polypterus ornatipinnis",
                                    "info": "The ornate bichir is a large bichir species found in central and east Africa. It is a primitive air-breathing fish, with a set of primitive lungs that allow it to breathe air from the surface of turbid and stagnant water, where bichirs often live.",
                                    "type": "Fish",
                                    "order": "Polypteriformes",
                                    "family": "Polypteridae",
                                    "genus": "Polypterus",
                                    "image_url": "https://i.imgur.com/9pms9UI.jpeg",
                                    "breeding": "Impossible",
                                    "region": "Africa",
                                    "holdings": {},
                                        },

                                    "Nile Crocodile": {
                                    "common": "Nile Crocodile",
                                    "scientific": "Crocodylus niloticus",
                                    "info": "The Nile crocodile is the second-largest species of crocodile, native to both mainland Africa and Madagascar. An indomitable apex predator, they prey on virtually anything within their range, and are regarded as one of the most dangerous crocodile species due to their aggression towards humans.",
                                    "type": "Reptile",
                                    "order": "Crocodilia",
                                    "family": "Crocodylidae",
                                    "genus": "Crocodylus",
                                    "image_url": "https://i.imgur.com/RRbLO0p.jpeg",
                                    "breeding": "Below Average",
                                    "region": "Africa",
                                    "holdings": {},
                                        },

                                    "Spotted Gar": {
                                    "common": "Spotted Gar",
                                    "scientific": "Lepisosteus oculatus",
                                    "info": "A medium-sized freshwater fish native to North America, the spotted gar is a pursuit predator that chases smaller fish such as minnows and shiners. Its long snout is perfect for this style of hunting, and their eggs are highly toxic, providing an effective defense against predators.",
                                    "type": "Fish",
                                    "order": "Lepisosteiformes",
                                    "family": "Lepisosteidae",
                                    "genus": "Lepisosteus",
                                    "image_url": "https://i.imgur.com/khtwzKo.jpeg",
                                    "breeding": "Impossible",
                                    "region": "North America",
                                    "holdings": {},
                                            },

                                     "Tentacled Snake": {
                                    "common": "Tentacled Snake",
                                    "scientific": "Erpeton tentaculatum",
                                    "info": "An aquatic snake native to Southeast Asia, this small species is mildly venomous and feeds primarily upon small freshwater fish. Its unique 'tentacles' on the front of the head act as limbs, helping the snakes feel around in cloudy water.",
                                    "type": "Reptile",
                                    "order": "Squamata",
                                    "family": "Homalopsidae",
                                    "genus": "Erpeton",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/335960148/original.jpg",
                                    "breeding": "Impossible",
                                    "region": "Asia",
                                    "holdings": {},
                                        },

                                "Amazonian Giant Centipede": {
                                "common": "Amazonian Giant Centipede",
                                "scientific": "Scolopendra gigantea",
                                "info": "This is the world's largest centipede, exceeding 1ft in length. Native to South America and the Caribbean, it is a voracious predator, able to overpower many smaller animals including insects, spiders, and even birds and bats. Unusually for a centipede, they are docile and can be handled somewhat.",
                                "type": "Invertebrate",
                                "order": "Scolopendromorpha",
                                "family": "Scolopendridae",
                                "genus": "Scolopendra",
                                "image_url": "https://i0.wp.com/adlayasanimals.wordpress.com/wp-content/uploads/2021/01/1920px-spiders_genova_-_scolopendra_gigantea-e1610739899911.jpg?fit=1200%2C675&ssl=1",
                                "breeding": "Difficult",
                                "region": "North America, South America",
                                    "holdings": {
                                        "North America": "1.1 - Essex County Zoo", },
                                },

                                "Eastern Gray Squirrel": {
                                "common": "Eastern Gray Squirrel",
                                "scientific": "Sciurus carolinensis",
                                "info": "A common and ubiquitous species in eastern North America, the eastern gray squirrel is an important forest regenerator in its natural range, helping to maintain the health and ecological diversity of temperate forests. Unfortunately, this species has also been introduced outside of its range, and is outcompeting native red squirrels in the United Kingdom.",
                                "type": "Mammal",
                                "order": "Rodentia",
                                "family": "Sciuridae",
                                "genus": "Sciurus",
                                "image_url": "https://wildadirondacks.org/images/Adirondack-Mammals-Gray-Squirrel-Sciurus-carolinensis-Country-Club-Lane-18-November-2020-71.jpg",
                                "breeding": "Easy",
                                "region": "North America",
                                    "holdings": {
                                        "North America": "1.0 - Essex County Zoo", },
                                },

                                "Canada Goose": {
                                "common": "Canada Goose",
                                "scientific": "Branta canadensis",
                                "info": "One of the most adaptable North American waterfowl, the Canada goose is somewhat infamous for its ability to live in human-inhabited areas. It is the subject of a low-stakes human-wildlife conflict, as people find their aggressive defense of their goslings as a nuisance. They have also been introduced to Europe and have become invasive there.",
                                "type": "Bird",
                                "order": "Anseriformes",
                                "family": "Anatidae",
                                "genus": "Branta",
                                "image_url": "https://static.inaturalist.org/photos/229398131/large.jpg",
                                "breeding": "Easy",
                                "region": "North America",
                                    "holdings": {
                                        "North America": "0.2 - Essex County Zoo", },
                                },

                                "Corn Snake": {
                                "common": "Corn Snake",
                                "scientific": "Pantherophis guttatus",
                                "info": "A small colubrid from North America, the corn snake is sometimes regarded as a pest, but in reality is actually beneficial to farmers as they eat crop-destroying rodents. Their common name comes from the fact that they are known for living near grain stores, preying upon the mice and rats that feed on them.",
                                "type": "Reptile",
                                "order": "Squamata",
                                "family": "Colubridae",
                                "genus": "Pantherophis",
                                "image_url": "https://static.inaturalist.org/photos/457255500/large.jpg",
                                "breeding": "Easy",
                                "region": "North America",
                                    "holdings": {
                                        "North America": "0.3 - Tri-State Zoo & Aquarium", },
                                },

                                    "White-Lipped Pit Viper": {
                                    "common": "White-Lipped Pit Viper",
                                    "scientific": "Trimeresurus albolabris",
                                    "info": "One of the most well-known species in its genus, the white-lipped pit viper has highly variable coloration. These variants include green, mint, and striped. They are found in east, southeast, and south Asia, making them one of the most widely distributed Trimeresurus species.",
                                    "type": "Reptile",
                                    "order": "Squamata",
                                    "family": "Viperidae",
                                    "genus": "Trimeresurus",
                                    "images": [
                                        {"label": "Green variant", "url": "https://i.imgur.com/OZQe3oQ.jpeg"}
                                    ],
                                    "breeding": "Below Average",
                                    "region": "Asia",
                                    "holdings": {
                                        "North America": "1.0 - Essex County Zoo", },
                                },
                                    "Mediterranean Banded Centipede": {
                                        "common": "Mediterranean Banded Centipede",
                                        "scientific": "Scolopendra cingulata",
                                        "info": "One of the more common scolopendrids in captivity, the Mediterranean banded centipede has a mild venom compared to fellow scolopendromorphs. Only growing 7 inches, it is also one of the smaller species in its genus. Most of the time they lay burrowed in dark, damp environments like leaf litter.",
                                        "type": "Invertebrate",
                                        "order": "Scolopendromorpha",
                                        "family": "Scolopendridae",
                                        "genus": "Scolopendra",
                                        "image_url": "https://i.imgur.com/b2wgjh0.jpeg",
                                        "breeding": "Below Average",
                                        "region": "Europe, Asia, Africa",
                                        "holdings": {
                                            "North America": "1.1 - Essex County Zoo", },
                                    },
                                        "Vietnamese Giant Centipede": {
                                            "common": "Vietnamese Giant Centipede",
                                            "scientific": "Scolopendra dehaani",
                                            "info": "A large, highly aggressive centipede species described as having a 'nasty temperament', the Vietnamese giant centipede can be found in regions of south, southeast, and east Asia. Typically living for 5 to 6 years, they typically forage for prey such as invertebrates, but have on occasion taken larger animals like frogs and snakes.",
                                            "type": "Invertebrate",
                                            "order": "Scolopendromorpha",
                                            "family": "Scolopendridae",
                                            "genus": "Scolopendra",
                                            "image_url": "https://i.imgur.com/iMLj7MZ.jpeg",
                                            "breeding": "Below Average",
                                            "region": "Asia",
                                            "holdings": {
                                                "North America": "1.1 - Essex County Zoo", },
                                        },
                                            "Regal Jumper": {
                                            "common": "Regal Jumper",
                                            "scientific": "Phidippus regius",
                                            "info": "The largest jumping spider in eastern North America, the regal jumper is often found in the private trade due to its hardiness and various attractive color forms. Typically preferring open areas, they sleep in silken nests at night, often in enclosed areas where it is safe from predators.",
                                            "type": "Invertebrate",
                                            "order": "Araneae",
                                            "family": "Salticidae",
                                            "genus": "Phidippus",
                                                "images": [
                                                    {"label": "Black variant", "url": "https://cdn.store-assets.com/s/727929/i/23312655.jpg?width=1024"}
                                                ],
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                            },
                                            "Common Pillbug": {
                                            "common": "Common Pillbug",
                                            "scientific": "Armadillidium vulgare",
                                            "info": "The most extensively studied terrestrial isopod, the common pillbug can be found natively in Europe. Introduced widely globally, it is a hardy and adaptable species which makes it ideal for the private trade, and as such is the most popular beginner isopod kept.",
                                            "type": "Invertebrate",
                                            "order": "Isopoda",
                                            "family": "Armadillidiidae",
                                            "genus": "Armadillidium",
                                            "image_url": "https://i.imgur.com/J5e8sXF.jpeg",
                                            "breeding": "Very Easy",
                                            "region": "Europe",
                                                "holdings": {
                                                    "North America": "20 - Essex County Zoo", },
                                            },
                                            "Tiger": {
                                            "common": "Tiger",
                                            "scientific": "Panthera tigris",
                                            "info": "One of the most famous animals globally, the tiger is considered as part of Asia's charismatic megafauna. Native originally to much of the continent, its range has shrunk due to human-wildlife conflict, and now the last strongholds for the species are in South Asia, Southeast Asia, and North Asia. It has been extirpated from much of East and Central Asia.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Panthera",
                                            "images": [
                                                {"label": "Malayan tiger (jacksoni)", "url": "https://i.imgur.com/tL6DNMj.jpeg"}
                                            ],
                                            "breeding": "Difficult",
                                            "region": "Asia",
                                            "holdings": {},
                                            },
                                            "Lar Gibbon": {
                                            "common": "Lar Gibbon",
                                            "scientific": "Hylobates lar",
                                            "info": "An endangered gibbon, the lar gibbon (or simply lar) can be found natively in western Indochina in mainland Southeast Asia. It is a commonly kept gibbon species and is regarded as one of the more better-known species, due to its loud calls and distinctive coloration.",
                                            "type": "Mammal",
                                            "order": "Primates",
                                            "family": "Hylobatidae",
                                            "genus": "Hylobates",
                                            "image_url": "https://i.imgur.com/siDuVY7.jpeg",
                                            "breeding": "Below Average",
                                            "region": "Asia",
                                            "holdings": {},
                                            },
                                            "Common Eland": {
                                            "common": "Common Eland",
                                            "scientific": "Taurotragus oryx",
                                            "info": "The common eland is the second-largest antelope species in the world, right behind its cousin the giant eland. Found throughout east and southern Africa, they live in large herds of up to 500 individuals, feeding upon various types of plants. They are known to both browse and graze to obtain food.",
                                            "type": "Mammal",
                                            "order": "Artiodactyla",
                                            "family": "Bovidae",
                                            "genus": "Taurotragus",
                                            "image_url": "https://i.imgur.com/udd7w6M.jpeg",
                                            "breeding": "Average",
                                            "region": "Africa",
                                            "holdings": {},
                                            },
                                            "Sika Deer": {
                                            "common": "Sika Deer",
                                            "scientific": "Cervus nippon",
                                            "info": "The sika deer has a hugely disjunct range, from patches of Vietnam to Japan and Russia. There are many subspecies, some of which are highly abundant and some of which are extremely endangered. They, like several other deer species, have been introduced to various locations for hunting purposes and have become invasive.",
                                            "type": "Mammal",
                                            "order": "Artiodactyla",
                                            "family": "Cervidae",
                                            "genus": "Cervus",
                                            "image_url": "https://i.imgur.com/j2Uqt3y.jpeg",
                                            "breeding": "Easy",
                                            "region": "Asia",
                                            "holdings": {},
                                            },
                                            "Bactrian Camel": {
                                            "common": "Bactrian Camel",
                                            "scientific": "Camelus bactrianus",
                                            "info": "The domestic form of the wild Bactrian camel, the Bactrian camel has been exploited for thousands of years. They are more cold-hardy than dromedary camels and have been used by peoples of Central and East Asia for various purposes up to the modern period.",
                                            "type": "Mammal",
                                            "order": "Artiodactyla",
                                            "family": "Camelidae",
                                            "genus": "Camelus",
                                            "image_url": "https://pictureanimal.com/wiki-image/1080/152345814335750145.jpeg",
                                            "breeding": "Easy",
                                            },
                                            "Serval": {
                                            "common": "Serval",
                                            "scientific": "Leptailurus serval",
                                            "info": "A small wild cat native to Africa, the serval is a solitary carnivore with a wide range, stretching from west to southern Africa. Their main diet consists of rodents and other small mammals, but they will also take birds and small ungulates occasionally. They have been crossbred with domestic cats to create savannah cats.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Leptailurus",
                                            "image_url": "https://i.imgur.com/CbgCZ3U.jpeg",
                                            "breeding": "Average",
                                            "region": "Africa",
                                                "holdings": {
                                                    "North America": "1.0 - Riverside Zoo", },
                                            },
                                            "Bennett's Wallaby": {
                                            "common": "Bennett's Wallaby",
                                            "scientific": "Notamacropus rufogriseus",
                                            "info": "An abundant wallaby species native to parts of eastern and southern Australia, the Bennett's wallaby has been introduced to various locations in New Zealand and Europe. A mainly solitary species, Bennett's wallabies will gather together on occasion when there is plentiful food or water. They are a mainly nocturnal animal.",
                                            "type": "Mammal",
                                            "order": "Diprotodontia",
                                            "family": "Macropodidae",
                                            "genus": "Notamacropus",
                                            "image_url": "https://i.imgur.com/OGXBCOE.jpeg",
                                            "breeding": "Easy",
                                            "region": "Oceania",
                                                "holdings": {
                                                    "North America": "1.2 - Riverside Zoo",
                                                    "Europe": [
                                                        "1.2 - Brussels Zoo",
                                                    ]
                                                },
},
                                            "Rusty-Spotted Cat": {
                                            "common": "Rusty-Spotted Cat",
                                            "scientific": "Prionailurus rubiginosus",
                                            "info": "One of the smallest members of the cat family, the rusty-spotted cat is native to south Asia, from northern India to Sri Lanka. Very little is known about their ecology in the wild due to their elusiveness, and they feed mainly on rodents, birds, lizards, frogs, and insects. Captive males and females scent-mark their home range by spraying urine.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Prionailurus",
                                            "images": [
                                                {"label": "Sri Lankan rusty-spotted cat (phillipsi)", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/108527369/large.jpg"}
                                            ],
                                            "breeding": "Difficult",
                                            "region": "Asia",
                                            "holdings": {},
                                            },
                                            "Eurasian Lynx": {
                                            "common": "Eurasian Lynx",
                                            "scientific": "Lynx lynx",
                                            "info": "The Eurasian lynx is a species of lynx native to a huge swath of Eurasia, from France to Siberia. Despite this, it is threatened by habitat loss, poaching, and depletion of prey. Reintroduction projects have begun in much of western Europe, which have seen an increase in forest health and a depletion of overabundant prey populations.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Lynx",
                                            "image_url": "https://www.euronatur.org/fileadmin/_processed_/4/c/csm_Luchs_sitzt_auf_Fels-Christof_Wermter__869aa2a067.jpg",
                                            "breeding": "Difficult",
                                            "region": "Europe, Asia",
                                            "holdings": {},
                                            },
                                            "Fishing Cat": {
                                            "common": "Fishing Cat",
                                            "scientific": "Prionailurus viverrinus",
                                            "info": "The fishing cat is unique amongst felids in the sense that is is a specialized piscivore. A strong swimmer, it can swim long distances, which is unusual for a small cat. Typically living in wetland environments, it is listed as Vulnerable on the IUCN Red List due to substantial loss of wetlands in its native range.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Prionailurus",
                                            "image_url": "https://animals.sandiegozoo.org/sites/default/files/inline-images/fishing_cat02.jpg",
                                            "breeding": "Difficult",
                                            "region": "Asia",
                                                "holdings": {
                                                    "Europe": "2.0 - Parque Zoologico de Clear Coast", },
},
                                            "Ocelot": {
                                            "common": "Ocelot",
                                            "scientific": "Leopardus pardalis",
                                            "info": "A small cat found in the Americas, the ocelot is a generalist mesopredator, feeding upon a variety of different small animals, and even plant matter on occasion. An adaptable species, its preferred habitats are desert and rainforest with an abundance of prey and water. It is a crepuscular species that hunts under cover of darkness.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Leopardus",
                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/518453211/original.jpg",
                                            "breeding": "Below Average",
                                            "region": "North America, South America",
                                            "holdings": {},
                                            },
                                            "Pallas's Cat": {
                                            "common": "Pallas's Cat",
                                            "scientific": "Otocolobus manul",
                                            "info": "The Pallas's cat can be found in mountainous areas of west, central, south, and east Asia, from the Caucasus in the west to Russia in the east. A solitary species, it is a highly specialized predator of small mammals including rodents and shrews. It is also called 'manul' in the Mongolian language, lending it is species name.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Otocolobus",
                                                "images": [
                                                    {"label": "Siberian Pallas's cat (manul)", "url": "https://i.imgur.com/1V0pfqe.jpeg"}
                                                ],
                                            "breeding": "Below Average",
                                            "region": "Asia",
                                            "holdings": {},
                                            },
                                            "Caracal": {
                                            "common": "Caracal",
                                            "scientific": "Caracal caracal",
                                            "info": "A distinctive nocturnal small cat found throughout Africa and parts of Asia, the caracal is well-known for its jumping ability, which allows it to catch birds in flight. However, most of its diet actually consists of small mammals. They are often thought of as a lynx, but are placed in a different genus.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Felidae",
                                            "genus": "Caracal",
                                            "image_url": "https://i.imgur.com/r6bObbu.png",
                                            "breeding": "Below Average",
                                            "region": "Asia, Africa",
                                                "holdings": {
                                                    "North America": "0.1 - Riverside Zoo", },
},
                                            "Atlantic Puffin": {
                                            "common": "Atlantic Puffin",
                                            "scientific": "Fratercula arctica",
                                            "info": "One of the most famous seabirds, the Atlantic puffin is found on both sides of the North Atlantic, and is most abundant in Iceland. They are devoted parents, flying many miles out to sea to obtain food for their young, and their striking color has given them the nickname 'sea parrot.'",
                                            "type": "Bird",
                                            "order": "Charadriiformes",
                                            "family": "Alcidae",
                                            "genus": "Fratercula",
                                            "image_url": "https://i.imgur.com/MMlstTU.jpeg",
                                            "breeding": "Difficult",
                                            "region": "North America, Europe, Africa",
                                            "holdings": {},
                                            },
                                            "North American River Otter": {
                                            "common": "North American River Otter",
                                            "scientific": "Lontra canadensis",
                                            "info": "A semiaquatic mustelid endemic to North America, the North American river otter is a famous symbol of the wetlands of the continent. A mesopredator feeding on a variety of creatures both aquatic and terrestrial, they are regarded as an important indicator species, as otters do not tolerate heavily polluted waterways.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Mustelidae",
                                            "genus": "Lontra",
                                            "image_url": "https://www.ndow.org/wp-content/uploads/2021/10/lontra_canadensis.jpeg",
                                            "breeding": "Average",
                                            "region": "North America",
                                                "holdings": {
                                                    "North America": "1.2 - Tri-State Zoo & Aquarium ", },
                                            },
                                            "Common Snapping Turtle": {
                                            "common": "Common Snapping Turtle",
                                            "scientific": "Chelydra serpentina",
                                            "info": "One of the largest freshwater turtles in North America, the common snapping turtle is a high-ranking predator, feeding mainly on aquatic animals but also a variety of terrestrial species as well. Regarded as a dangerous animal, it is in fact generally docile unless provoked.",
                                            "type": "Reptile",
                                            "order": "Testudines",
                                            "family": "Chelydridae",
                                            "genus": "Chelydra",
                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/72712185/original.jpeg",
                                            "breeding": "Below Average",
                                            "region": "North America",
                                            "holdings": {},
                                            },
    "African Penguin": {
        "common": "African Penguin",
        "scientific": "Spheniscus demersus",
        "info": "The African penguin is critically endangered due to oil spills, poaching, and overfishing of its preferred prey. A large worldwide captive breeding program has been initiated for this species, which is endemic to the southern coast of Africa.",
        "type": "Bird",
        "order": "Sphenisciformes",
        "family": "Spheniscidae",
        "genus": "Spheniscus",
        "image_url": "https://www.ecoregistros.org/site/images/dataimages/2016/11/19/175994/pinguino-del-cabo--8-.JPG",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "North America": [
                "5.5 - Las Vegas Zoo"
            ],
            "Europe": [
                "6.6 - Parque Zoologico de Clear Coast"
            ]
        }
                                            },
                                            "Mandarin Duck": {
                                            "common": "Mandarin Duck",
                                            "scientific": "Aix galericulata",
                                            "info": "This colorful duck, closely related to the wood ducks of North America, can be found natively in east Asia. It has been introduced to parts of Europe as an ornamental species, but it has become invasive in wetland environments.",
                                            "type": "Bird",
                                            "order": "Anseriformes",
                                            "family": "Anatidae",
                                            "genus": "Aix",
                                            "image_url": "https://i.imgur.com/3htYRVe.jpeg",
                                            "breeding": "Easy",
                                            "region": "Asia",
                                            "holdings": {},
                                            },
                                            "American Toad": {
                                            "common": "American Toad",
                                            "scientific": "Anaxyrus americanus",
                                            "info": "The most abundant toad in its range, the American toad is a common sight in woodlands with abundant prey and clean water. This species, like all toads, is mildly poisonous which is a defense mechanism that gives it a bad taste, dissuading predators from consuming them.",
                                            "type": "Amphibian",
                                            "order": "Anura",
                                            "family": "Bufonidae",
                                            "genus": "Anaxyrus",
                                            "image_url": "https://i.imgur.com/HUA4coA.jpeg",
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                            },
                                            "American Bullfrog": {
                                            "common": "American Bullfrog",
                                            "scientific": "Lithobates catesbeianus",
                                            "info": "A large frog famed for its hardiness, the American bullfrog is a common sight in eastern North America. It is adaptable for an amphibian and can inhabit a wide range of habitats, including human-disturbed areas, which gives it an edge compared to other native amphibians.",
                                            "type": "Amphibian",
                                            "order": "Anura",
                                            "family": "Ranidae",
                                            "genus": "Lithobates",
                                            "image_url": "https://www.citizenscience.lu/images/content/Bioindicator_Species/invasive_species/invasive_animals/Ochsenfrosch.jpg",
                                            "breeding": "Average",
                                            "region": "North America",
                                                "holdings": {
                                                    "North America": "1.1 - Tri-State Zoo & Aquarium ", },
                                            },
                                            "American Lobster": {
                                            "common": "American Lobster",
                                            "scientific": "Homarus americanus",
                                            "info": "The world's largest crustacean, the American lobster is also the largest of all arthropods. Found exclusively on the northeastern coast of North America, this huge invertebrate feeds mainly on mollusks, echinoderms, and marine worms. It has several rare color variants, which are often displayed in public aquaria.",
                                            "type": "Invertebrate",
                                            "order": "Decapoda",
                                            "family": "Nephropidae",
                                            "genus": "Homarus",
                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/75691769/original.jpg",
                                            "breeding": "Impossible",
                                            "region": "North America",
                                                "holdings": {
                                                    "North America": "3 - Tri-State Zoo & Aquarium", },
                                            },
                                            "Demoiselle Crane": {
                                            "common": "Demoiselle Crane",
                                            "scientific": "Grus virgo",
                                            "info": "The demoiselle crane is the smallest species of crane, native to Asia. They are well-known for their long migrations over the Himalayan mountains that allow them to migrate to their breeding sites in Central Asia. Interestingly, birds native to western Eurasia will migrate to Africa during the wintertime.",
                                            "type": "Bird",
                                            "order": "Gruiformes",
                                            "family": "Gruidae",
                                            "genus": "Grus",
                                            "image_url": "https://i.imgur.com/KHQQQ12.jpeg",
                                            "breeding": "Below Average",
                                            "region": "Asia, Africa",
                                            "holdings": {},
                                        },

    "Domestic Chicken": {
        "common": "Domestic Chicken",
        "scientific": "Gallus gallus domesticus",
        "info": "The most numerous of all domesticated animals, the domestic chicken is estimated to have 50 billion individuals alive, mostly for human consumption in the form of meat and eggs, but domestic chickens are also used as companion animals. They are the domesticated form of the red junglefowl and first originated in southeast Asia.",
        "type": "Bird",
        "order": "Galliformes",
        "family": "Phasianidae",
        "genus": "Gallus",
        "images": [
            {"label": "Silkie", "url": "https://i.imgur.com/8VBhO1m.jpeg"},
            {"label": "Italiana", "url": "https://i.imgur.com/THJldGX.jpeg"},
        ],
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/274681663/large.jpg",
        "breeding": "Very Easy",
        },

        "Domestic Sheep": {
        "common": "Domestic Sheep",
        "scientific": "Ovis aries",
        "info": "The domestic sheep originated in Eurasia, descending from the mouflon. They are commonly kept for the purposes of their wool and meat, and due to their close association with humans are well-known and culturally important to many places around the world.  Sheep are thought of as unintelligent, but are actually considered one of the smartest domesticated animals.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Bovidae",
        "genus": "Ovis",
        "images": [
        {"label": "Jacob's Sheep", "url": "https://i.imgur.com/LgecxXj.jpeg"},
        ],
        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/241466812/large.jpeg",
        "breeding": "Very Easy",
        },

    "Virginia Opossum": {
        "common": "Virginia Opossum",
        "scientific": "Didelphis virginiana",
        "info": "The only marsupial found in the United States, the Virginia opossum is a solitary nocturnal species well-known for its habit of 'playing possum', where when attacked by a predator it pretends to be dead in order to dissuade predation. They are devoted parents, with mother opossums caring for their young for 4-5 months.",
        "type": "Mammal",
        "order": "Didelphimorphia",
        "family": "Didelphidae",
        "genus": "Didelphis",
        "image_url": "https://nhpbs.org/wild/images/virginiaopossumforestrydavidcapeaert.jpg",
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {
            "North America": [
                "1.1 - Karl Eberhard's Pine Lake Zoo",
                "1.0 - Riverside Zoo",
                "1.0.2.0 - Tri-State Zoo & Aquarium"
            ]
        }
    },
        "Fancy Rat": {
        "common": "Fancy Rat",
        "scientific": "Rattus norvegicus domestica",
        "info": "The domesticated form of the brown rat, the fancy rat was originally bred for blood sports. When this was phased out, they became popular pets, renowned for their intelligence, playfulness, and cleanliness. Fancy rats come in a variety of morphs, bred into the subspecies for generations. In some areas they are banned due to the potential for invasiveness.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Muridae",
        "genus": "Rattus",
        "image_url": "https://i.imgur.com/LH1tF9o.jpeg",
        "breeding": "Very Easy",
        },

        "Gladiator Stag Beetle": {
        "common": "Gladiator Stag Beetle",
        "scientific": "Homoderus gladiator",
        "info": "A stag beetle native to Africa, the gladiator stag beetle is regarded as difficult to breed in captivity due to its preference for laying eggs in wood. Little is known about this species in the wild and it is rarely held in captivity.",
        "type": "Invertebrate",
        "order": "Coleoptera",
        "family": "Lucanidae",
        "genus": "Homoderus",
        "image_url": "https://thespidershop.co.uk/wp-content/uploads/2018/06/H_gladiator.jpg",
        "breeding": "Difficult",
        "region": "Africa",
        "holdings": {},
            },

        "Japanese Stag Beetle": {
        "common": "Japanese Stag Beetle",
        "scientific": "Dorcus hopei",
        "info": "A large beetle native to eastern Asia, the Japanese stag beetle remains in its larval stage for 1-2 years, and emerge as adults afterwards. The adults live for around 3-5 years and feed upon plant matter and fruit. A unique trait of this species is the antifreeze proteins in its body, which allows it to survive the cold winters.",
        "type": "Invertebrate",
        "order": "Coleoptera",
        "family": "Lucanidae",
        "genus": "Dorcus",
            "images": [
                {"label": "Subspecies binodulosus", "url": "https://www.joelsartore.com/wp-content/uploads/stock/INS026/INS026-00202-1920x1279.jpg"},
            ],
        "breeding": "Difficult",
        "region": "Asia",
        "holdings": {},
            },

        "Taiwanese Stag Beetle": {
        "common": "Taiwanese Stag Beetle",
        "scientific": "Cyclommatus mniszechi",
        "info": "A distinctive beetle species endemic to Taiwan, the Taiwanese stag beetle feeds upon tree bark as a juvenile and a mixture of fruits as an adult. It is regarded as an easier species to breed than many stag beetles, and its distinctive golden appearance makes it highly prized by invertebrate keepers.",
        "type": "Invertebrate",
        "order": "Coleoptera",
        "family": "Lucanidae",
        "genus": "Dorcus",
        "image_url": "https://richardsinverts-store.com/cdn/shop/products/i-img1200x1200-1624245649b8hpbb29980.jpg?v=1642870614&width=1445",
        "breeding": "Average",
        "region": "Asia",
        "holdings": {},
        },

        "Titan Stag Beetle": {
        "common": "Titan Stag Beetle",
        "scientific": "Serrognathus titanus",
        "info": "This stag beetle is regarded as one of the better-known and popular species in its group. Widely distributed throughout south, east and southeast Asia, they live for 1-2 years and inhabits a wide variety of habitats, as evidenced by its wide range. There are potentially 39 subspecies of this beetle, but further research is required.",
        "type": "Invertebrate",
        "order": "Coleoptera",
        "family": "Lucanidae",
        "genus": "Serrognathus",
            "images": [
                {"label": "Subspecies palawanicus", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/572107599/original.jpg"},
                {"label": "Subspecies titanus", "url": "https://www.pierrewildlife.com/wp-content/uploads/2024/10/Serrognathus-titanus-titanus-2-2.jpg"}
            ],
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
        },

        "Japanese Rhinoceros Beetle": {
        "common": "Japanese Rhinoceros Beetle",
        "scientific": "Allomyrina dichotoma",
        "info": "One of the most well-known species of beetles, the Japanese rhinoceros beetle is found in Asia, from India to Japan. Adults feed on tree sap and bore through the wood to get to it. Males use their large horns to fight each other for mates or territory.",
        "type": "Invertebrate",
        "order": "Coleoptera",
        "family": "Scarabaeidae",
        "genus": "Allomyrina",
        "image_url": "https://i.imgur.com/wJ9xgqU.jpeg",
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
            },

        "Metallic Stag Beetle": {
        "common": "Metallic Stag Beetle",
        "scientific": "Cyclommatus metallifer",
        "info": "An attractively-colored beetle with several color morphs, the metallic stag beetle endemic to Indonesia. Feeding on sap from plants, they may also feed on flowers and fruit. The males vary wildly in size, from 26 to 100 millimeters in length.",
        "type": "Invertebrate",
        "order": "Coleoptera",
        "family": "Lucanidae",
        "genus": "Cyclommatus",
        "images": [
            {"label": "Supernova color variant", "url": "https://davidsbeetles.com/cdn/shop/articles/metallifer5_1_of_1.jpg?v=1667521313"},
        ],
        "breeding": "Difficult",
        "region": "Asia",
        "holdings": {},
        },

        "Godzilla Isopod": {
        "common": "Godzilla Isopod",
        "scientific": "Spherillo sp. Godzilla",
        "info": "One of the largest of the 'spiky isopods', the Godzilla isopod is an extremely rare species originating from southeast Asia. They are known for their defense mechanism, where they hiss at potential predators by rubbing their legs against the interior of their exoskeletons.",
        "type": "Invertebrate",
        "order": "Isopoda",
        "family": "Armadillidae",
        "genus": "Spherillo",
        "image_url": "https://i.imgur.com/1BQ5HNo.jpeg",
        "breeding": "Difficult",
        "region": "Asia",
        "holdings": {},
        },

        "Javan Leaf Insect": {
        "common": "Javan Leaf Insect",
        "scientific": "Pulchriphyllium pulchrifolium",
        "info": "A typical member of its genus, the Javan leaf insect is often found in a green or brownish coloration. Like all leaf insects they imitate leaves for camoflauge purposes, protecting them from predators.",
        "type": "Invertebrate",
        "order": "Phasmatodea",
        "family": "Phylliidae",
        "genus": "Pulchriphyllium",
        "image_url": "https://www.phasmatodea.com/sites/default/files/speciesgallery/phyllium/bioculatum-gray-1832/mixed/idbioculatum-gray-1832.jpg",
        "breeding": "Below Average",
        "region": "Asia",
        "holdings": {},
            },

            "Camoflauge Isopod": {
            "common": "Camoflauge Isopod",
            "scientific": "Troglodillo rotundatus",
            "info": "A rare isopod endemic to China, the camoflauge isopod is scarce and highly sought after in the isopod hobby. Typically preferring drier environments with thin layers of substrate, they can command high prices as they are extremely difficult to breed in captivity.",
            "type": "Invertebrate",
            "order": "Isopoda",
            "family": "Armadillidae",
            "genus": "Troglodillo",
            "image_url": "https://cdn.isopod.site/2022/02/P9279477x.jpg",
            "breeding": "Difficult",
            "region": "Asia",
            "holdings": {},
            },

            "Yellowline Arrow Crab": {
            "common": "Yellowline Arrow Crab",
            "scientific": "Stenorhynchus seticornis",
            "info": "Also known as simply 'arrow crab', the yellowline arrow crab is a predatory crustacean native to the Caribbean. A nocturnal species averse to sunlight, the yellowline arrow crab is a predator of feather duster worms and other reef invertebrates. They are commonly kept in private aquaria to hunt bristle worms. They sometimes serve as a cleaner for animals such as moray eels and squirrelfish.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Inachidae",
            "genus": "Stenorhynchus",
            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/176933317/large.jpg",
            "breeding": "Impossible",
            "region": "North America, South America",
            "holdings": {},
                },

            "Pom Pom Crab": {
            "common": "Pom Pom Crab",
            "scientific": "Lybia tessellata",
            "info": "This small nocturnal crab is native to the Indo-Pacific. Living on coral reefs, it has a unique adaptation, it lives commensally with a small species of sea anemone that provides it protection, while the crab allows the sea anemone more mobility. They typically cling to corals with their highly adapted legs.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Xanthidae",
            "genus": "Lybia",
            "image_url": "https://static.inaturalist.org/photos/30267880/large.jpg",
            "breeding": "Impossible",
            "region": "Asia, Africa, Oceania",
            "holdings": {},
                },

            "Emerald Crab": {
            "common": "Emerald Crab",
            "scientific": "Mithraculus sculptus",
            "info": "Commonly collected from its native range for the aquarium trade, the emerald crab is a hardy omnivorous scavenger that typically selects a portion of rock to claim as its own. They are aggressive towards conspecifics and like many crabs are mostly nocturnal.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Majidae",
            "genus": "Mithraculus",
            "image_url": "https://www.animalspot.net/wp-content/uploads/2018/10/Green-Emerald-Crab.jpg",
            "breeding": "Impossible",
            "region": "North America, South America",
            "holdings": {},
                },

            "Strawberry Crab": {
            "common": "Strawberry Crab",
            "scientific": "Neoliomera pubescens",
            "info": "A small bright pink crab with a carapace width of 2 inches, the strawberry crab is a nocturnal scavenger. It can be found from eastern Africa to Hawaii, and is sometimes collected for the aquarium trade for its bright coloration and peaceful nature.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Xanthidae",
            "genus": "Neoliomera",
            "image_url": "https://media.masterfisch.com/81203-thickbox_default/strawberry-crab.jpg",
            "breeding": "Impossible",
            "region": "Asia, Africa, Oceania",
            "holdings": {},
            },

            "Blue Leg Hermit Crab": {
            "common": "Blue Leg Hermit Crab",
            "scientific": "Clibanarius tricolor",
            "info": "A small hermit crab less than 1 inch in length, the blue leg hermit crab can be found in shallow reefs in the Caribbean. They are an important detritivore, consuming dead plants, animals, and algaes that would otherwise clog the reef and make in uninhabitable for other animals.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Diogenidae",
            "genus": "Clibanarius",
            "image_url": "https://tropicalfishplus.com/cdn/shop/products/Clibanarius_tricolor_9_BG_800x.jpg?v=1605659918",
            "breeding": "Impossible",
            "region": "North America, South America",
            "holdings": {},
                },

            "Blue Line Hermit Crab": {
            "common": "Blue Line Hermit Crab",
            "scientific": "Calcinus elegans",
            "info": "A small, brightly colored hermit crab found in the Indo-Pacific, the blue line hermit crab is commonly utilized in the aquarium trade for ornamental and detritivorous purposes. Like many hermit crab species, the blue line hermit crab participates in shell exchanges, associating with other crabs to trade their shells.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Diogenidae",
            "genus": "Calcinus",
            "image_url": "https://www.thereefexperience.com/cdn/shop/products/blue_let_hermit_1024x1024_1024x_fbd6fd40-8845-4034-b05a-180889df77d2.jpg?v=1670618337",
            "breeding": "Impossible",
            "region": "Africa, Asia, Oceania",
            "holdings": {},
            },

            "Halloween Hermit Crab": {
            "common": "Halloween Hermit Crab",
            "scientific": "Ciliopagurus strigatus",
            "info": "The Halloween hermit crab gets its common name from its orange and yellow coloration. It is a voracious detritivore that eats algae, making it popular in the aquarium trade, but it is larger and more aggressive than many hermit crab species and therefore should be acquired with caution.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Diogenidae",
            "genus": "Ciliopagurus",
            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/46225358/large.jpg",
            "breeding": "Impossible",
            "region": "Africa, Asia, Oceania",
            "holdings": {},
            },

            "Scarlet Hermit Crab": {
            "common": "Scarlet Hermit Crab",
            "scientific": "Paguristes cadenati",
            "info": "A small hermit crab native to the Caribbean, the scarlet hermit crab is considered more peaceful than other hermits of its size, and is a voracious detritivore that consumes algae and animal matter. They can be found from the intertidal zone down to about 80m in the ocean.",
            "type": "Invertebrate",
            "order": "Decapoda",
            "family": "Diogenidae",
            "genus": "Paguristes",
            "image_url": "https://tropicalfishplus.com/cdn/shop/products/scarlet_hermit_c_4e7f2b6dbf345_1000x.jpg?v=1605661053",
            "breeding": "Impossible",
            "region": "North America, South America",
            "holdings": {},
            },

            "Common Grackle": {
                "common": "Common Grackle",
                "scientific": "Quiscalus quiscula",
                "info": "One of the most common and well-known North American songbirds, the common grackle is noted for the iridescence on its black feathers, which is especially pronounced in males. They are omnivorous, feeding upon insects and seeds primarily, and can be found natively east of the Rocky Mountains.",
                "type": "Bird",
                "order": "Passeriformes",
                "family": "Icteridae",
                "genus": "Quiscalus",
                "image_url": "https://i.imgur.com/cOH55iW.jpeg",
                "breeding": "Average",
                "region": ["North America"],  # list, not string
                "holdings": {},
                },

                "Painted Lady": {
                    "common": "Painted Lady",
                    "scientific": "Vanessa cardui",
                    "info": "The most widely distributed of all butterflies, the painted lady can be found on every continent except Oceania, Antarctica and South America. This species is typically intolerant of cold climates and therefore is known for its migrations, such as between North Africa and Europe.",
                    "type": "Invertebrate",
                    "order": "Lepidoptera",
                    "family": "Nymphalidae",
                    "genus": "Vanessa",
                    "image_url": "https://i.imgur.com/Y5rNXU6.jpeg",
                    "breeding": "Average",
                    "region": "North America, Europe, Asia, Africa",
                    "holdings": {},
                },

    "Pumpkinseed": {
        "common": "Pumpkinseed",
        "scientific": "Lepomis gibbosus",
        "info": "One of the most well known of the freshwater sunfish, the pumpkinseed is native to the eastern and central parts of North America, but has been introduced to various locations globally and has become invasive in certain places. It is a very popular species for recreational fishermen.",
        "type": "Fish",
        "order": "Centrarchiformes",
        "family": "Centrarchidae",
        "genus": "Lepomis",
        "image_url": "https://i.imgur.com/4apDvzz.jpeg",
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {
            "North America": "6 - Chicago City Aquarium"
        }
    },

                        "Yellow-Crowned Night Heron": {
                            "common": "Yellow-Crowned Night Heron",
                            "scientific": "Nyctanassa violacea",
                            "info": "A night heron endemic to the Americas, the yellow-crowned night heron feeds mainly on crustaceans, but also other small animals. Their mating cycle is tied to the life cycle of crabs, when they emerge, they mate and lay eggs. It is a widespread species without any major threats.",
                            "type": "Bird",
                            "order": "Pelecaniformes",
                            "family": "Ardeidae",
                            "genus": "Nyctanassa",
                            "image_url": "https://i.imgur.com/MCCY3Qv.jpeg",
                            "breeding": "Average",
                            "region": "North America, South America",
                            "holdings": {},
                                },

                            "Black Swallowtail": {
                            "common": "Black Swallowtail",
                            "scientific": "Papilio polyxenes",
                            "info": "Found throughout much of North America, the black swallowtail's caterpillars are adaptable and feed on a variety of plants within the family Apiaceae. Male butterflies will secure territories to use in mate location and courtship, and will defend this territory from other males.",
                            "type": "Invertebrate",
                            "order": "Lepidoptera",
                            "family": "Papilionidae",
                            "genus": "Papilio",
                            "image_url": "https://objects.liquidweb.services/images/201703/kevin_heffernan_15477106887_2516850b08_b.jpg",
                            "breeding": "Average",
                            "region": "North America, South America",
                            "holdings": {},
                                },

                            "Monarch": {
                                "common": "Monarch",
                                "scientific": "Danaus plexippus",
                                "info": "The monarch, or monarch butterfly, is probably the most well-known butterfly species in North America. While there are typical resident populations that don't move from their territories, there is a well-known migratory population that migrates to Mexico in the winter time that is listed as Vulnerable on the IUCN Red List.",
                                "type": "Invertebrate",
                                "order": "Lepidoptera",
                                "family": "Nymphalidae",
                                "genus": "Danaus",
                                "image_url": "https://i.imgur.com/MgTw1d2.jpeg",
                                "breeding": "Average",
                                "region": "North America, South America",
                                "holdings": {},
                                    },

                                "Common Mormon": {
                                    "common": "Common Mormon",
                                    "scientific": "Papilio polytes",
                                    "info": "A common swallowtail butterfly distributed across Asia, the common mormon is named because this species is polygamous, with males mating with multiple females. They are one of the species known to produce gynandromorphs, which are animals that display both male and female phenotypical traits.",
                                    "type": "Invertebrate",
                                    "order": "Lepidoptera",
                                    "family": "Papilionidae",
                                    "genus": "Papilio",
                                    "image_url": "https://live.staticflickr.com/7104/7127449655_3e9eab9544_b.jpg",
                                    "breeding": "Average",
                                    "region": "Asia",
                                    "holdings": {},
                                    },

                                    "Common Tiger": {
                                    "common": "Common Tiger",
                                    "scientific": "Danaus genutia",
                                    "info": "One of the most common butterflies in India, the common tiger can also be found in southeast Asia and Oceania. It, like other members of its genus, are considered unpalatable to predators, and their bright coloration advertises this to them.",
                                    "type": "Invertebrate",
                                    "order": "Lepidoptera",
                                    "family": "Nymphalidae",
                                    "genus": "Danaus",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/219936/original.jpg",
                                    "breeding": "Average",
                                    "region": "Asia, Oceania",
                                    "holdings": {},
                                        },

                                    "Eastern Honeybee": {
                                    "common": "Eastern Honeybee",
                                    "scientific": "Apis cerana",
                                    "info": "The second-most common of all honeybee species, the eastern honeybee can be found throughout south, east, and southeast Asia. The Japanese subspecies is known for its defense mechanism against Asian giant hornets, when threatened, the colony jumps on the hornet and fans their wings, raising the hornet's temperature until it dies.",
                                    "type": "Invertebrate",
                                    "order": "Hymenoptera",
                                    "family": "Apidae",
                                    "genus": "Apis",
                                    "image_url": "https://static.inaturalist.org/photos/2033079/large.jpg",
                                    "breeding": "Very Easy",
                                    "region": "Asia",
                                    "holdings": {},
                                    },

                                    "Plain Tiger": {
                                    "common": "Plain Tiger",
                                    "scientific": "Danaus chrysippus",
                                    "info": "Widespread across Asia, Africa, and Oceania, the plain tiger primarily consumes milkweed as a larvae, which gives it emetic properties as an adult. As adults they consume nectar like most butterflies. Preferring open and arid areas, this species adapts well to human interference, often thriving in cities and parks.",
                                    "type": "Invertebrate",
                                    "order": "Lepidoptera",
                                    "family": "Nymphalidae",
                                    "genus": "Danaus",
                                    "image_url": "https://i.imgur.com/yzpr3Kb.jpeg",
                                    "breeding": "Average",
                                    "region": "Asia, Africa, Oceania",
                                    "holdings": {},
                                    },

                                    "Tropical Leatherleaf Slug": {
                                    "common": "Tropical Leatherleaf Slug",
                                    "scientific": "Laevicaulis alte",
                                    "info": "A round, small slug species, the tropical leatherleaf slug is believed to be native to Africa, but has been introduced widely to many places in Asia and Oceania. It has several adaptations for surviving in dry, adverse conditions, such as a rounded shape with a small surface area and a narrow foot to reduce evaporation.",
                                    "type": "Invertebrate",
                                    "order": "Systellommatophora",
                                    "family": "Veronicellidae",
                                    "genus": "Laevicaulis",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/47874316/original.jpg",
                                    "breeding": "Average",
                                    "region": "Africa",
                                    "holdings": {},
                                    },

                                    "Florida Gar": {
                                    "common": "Florida Gar",
                                    "scientific": "Lepisosteus platyrhincus",
                                    "info": "This gar species is endemic to Georgia and Florida in the United States. Like many gar, it has highly toxic eggs which serve as a defense mechanism against potential predators, and it is a high-ranking predator in its natural range, feeding on fish, shrimp, and crayfish.",
                                    "type": "Fish",
                                    "order": "Lepisosteiformes",
                                    "family": "Lepisosteidae",
                                    "genus": "Lepisosteus",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/104542990/original.jpg",
                                    "breeding": "Impossible",
                                    "region": "North America",
                                        "holdings": {
                                            "North America": "3 - Tri-State Zoo & Aquarium", },
                                    },

    "Knobbed Whelk": {
                                    "common": "Knobbed Whelk",
                                    "scientific": "Busycon carica",
                                    "info": "A large, predatory sea snail native to the eastern coast of North America, the knobbed whelk feeds on oysters, clams, and other marine invertebrates. It migrates between deep and shallow water depending on the time of year. They have an excellent sense of smell and use it to look for prey.",
                                    "type": "Invertebrate",
                                    "order": "Neogastropoda",
                                    "family": "Busyconidae",
                                    "genus": "Busycon",
                                    "image_url": "https://static.inaturalist.org/photos/16337478/original.jpg",
                                    "breeding": "Impossible",
                                    "region": "North America",
                                    "holdings": {},
    },

    "Largemouth Bass": {
        "common": "Largemouth Bass",
        "scientific": "Micropterus nigricans",
        "info": "An iconic freshwater fish native to North America, the largemouth bass has become one of the most notorious invasive species globally, being introduced to places like Central America, Africa, Japan, and Canada. Largemouth bass are predators that feed on pretty much anything they can eat, preferring to live in heavily planted areas.",
        "type": "Fish",
        "order": "Centrarchiformes",
        "family": "Centrarchidae",
        "genus": "Micropterus",
        "image_url": "https://i.imgur.com/7oFLOlG.jpeg",
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {
            "North America": "1 - Chicago City Aquarium"
        }
    },

                                    "Red Swamp Crayfish": {
                                    "common": "Red Swamp Crayfish",
                                    "scientific": "Procambarus clarkii",
                                    "info": "Probably the most well-known of all crayfish, the red swamp crayfish is endemic to the United States and Mexico, but it has been introduced to other locations in North America and elsewhere. They are commonly farmed and consumed as food, and are hardy animals that can live in poor water conditions.",
                                    "type": "Invertebrate",
                                    "order": "Decapoda",
                                    "family": "Cambaridae",
                                    "genus": "Procambarus",
                                    "image_url": "https://i.imgur.com/nnuVUIV.jpeg",
                                    "breeding": "Below Average",
                                    "region": "North America",
                                    "holdings": {},
                                    },

                                    "Seminole Ramshorn Snail": {
                                    "common": "Seminole Ramshorn Snail",
                                    "scientific": "Planorbella duryi",
                                    "info": "A small snail species native to freshwater ecosystems in Florida, the Seminole ramshorn is a common aquarium pet, either intentional or accidental. They have been introduced to Hawaii, Europe, Palestine, and Nigeria by accident and have become invasive there.",
                                    "type": "Invertebrate",
                                    "order": "Unknown",
                                    "family": "Planorbidae",
                                    "genus": "Planorbella",
                                    "image_url": "https://www.garnelio.de/media/image/33/b1/52/garnelio-schnecke-braune-posthornschnecke-planorbella-duryi-duryi-2_430x430@2x.jpg",
                                    "breeding": "Very Easy",
                                    "region": "North America",
                                    "holdings": {},
                                    },

                                    "Arizona Blonde Tarantula": {
                                    "common": "Arizona Blonde Tarantula",
                                    "scientific": "Aphonopelma chalcodes",
                                    "info": "The Arizona blonde tarantula is one of the most common species kept in captivity. They are long-lived, docile, and have mild venom, making them an excellent first choice. They are typically nocturnal hunters, sleeping in a sling of silk during the day.",
                                    "type": "Invertebrate",
                                    "order": "Araneae",
                                    "family": "Theraphosidae",
                                    "genus": "Aphonopelma",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/4240836/large.jpg",
                                    "breeding": "Below Average",
                                    "region": "North America",
                                    "holdings": {
                                        "North America": "0.1 - Essex County Zoo", },
                                    },

                                    "Mallard": {
                                    "common": "Mallard",
                                    "scientific": "Anas platyrhynchos",
                                    "info": "One of the most well-known of all ducks, the mallard is native to North America, Europe, Asia, and north Africa. An adaptable and hardy species, it is often seen in human-inhabited areas and has been introduced to many locations, becoming invasive. This species has strong sexual dimorphism, with males having a green head and striking feathers, and the females having a more muted brown appearance.",
                                    "type": "Bird",
                                    "order": "Anseriformes",
                                    "family": "Anatidae",
                                    "genus": "Anas",
                                    "image_url": "https://i.imgur.com/LO4H6fs.jpeg",
                                    "breeding": "Easy",
                                    "region": "North America, Europe, Asia, Africa",
                                        "holdings": {
                                            "North America": "2.2 - Essex County Zoo", },
                                    },

                                    "Zander": {
                                    "common": "Zander",
                                    "scientific": "Sander lucioperca",
                                    "info": "A pikeperch native to western Eurasia, the zander has been introduced to various locations as a valuable sport fish. They are carnivorous and adults will hunt for smaller schooling fish such as smelt, ruffe, perch, and roach. They are long-lived fish, living up to 17 years.",
                                    "type": "Fish",
                                    "order": "Perciformes",
                                    "family": "Percidae",
                                    "genus": "Sander",
                                    "image_url": "https://i.imgur.com/PUM4c2p.jpeg",
                                    "breeding": "Difficult",
                                    "region": "Europe",
                                    "holdings": {},
                                    },

                                    "Stone Loach": {
                                    "common": "Stone Loach",
                                    "scientific": "Barbatula barbatula",
                                    "info": "The stone loach is a medium-sized loach that can grow up to 8.3 inches in length but typically only reaches 5 inches. They feed on small aquatic invertebrates like insect larvae and amphipods. They are nocturnal and feed mainly at night.",
                                    "type": "Fish",
                                    "order": "Cypriniformes",
                                    "family": "Nemacheilidae",
                                    "genus": "Barbatula",
                                    "image_url": "https://media-animals.earth.com/images/2022/08/17/626539844685148/barbatulabarbatula_31451673516346723.jpg",
                                    "breeding": "Difficult",
                                    "region": "Europe",
                                    "holdings": {},
                                },

                                    "European Sea Sturgeon": {
                                    "common": "European Sea Sturgeon",
                                    "scientific": "Acipenser sturio",
                                    "info": "A sturgeon growing up to 20 feet in length (but more commonly 1/5th of that size), the European sea sturgeon is critically endangered in the wild due to being caught as bycatch, habitat loss, and dam construction. This species spends most of its time in saltwater and spawns in freshwater.",
                                    "type": "Fish",
                                    "order": "Acipenseriformes",
                                    "family": "Acipenseridae",
                                    "genus": "Acipenser",
                                    "image_url": "https://i.imgur.com/1aElzLv.jpeg",
                                    "breeding": "Difficult",
                                    "region": "Europe, Asia",
                                    "holdings": {},
                                        },

                                        "Burbot": {
                                        "common": "Burbot",
                                        "scientific": "Lota lota",
                                        "info": "This distinctive species of fish is related to the cods. Native to subarctic regions of the Northern Hemisphere, it has a tolerance and preference for very cold water and breeds under ice. They are voracious predators, eating everything from insects to pike.",
                                        "type": "Fish",
                                        "order": "Gadiformes",
                                        "family": "Lotidae",
                                        "genus": "Lota",
                                        "image_url": "https://www.hlasek.com/foto/lota_lota_hf0194.jpg",
                                        "breeding": "Impossible",
                                        "region": "Europe, Asia",
                                        "holdings": {},
                                            },

                                        "Black Rat Snake": {
                                        "common": "Black Rat Snake",
                                        "scientific": "Pantherophis spiloides",
                                        "info": "A large colubrid growing to 6ft in length, the black rat snake is also known by the common name of gray rat snake, due to its variable coloration. They typically feed on rodents, birds, and eggs, and are considered helpful and beneficial to farmers due to their propensity to eat crop-destroying rodents.",
                                        "type": "Reptile",
                                        "order": "Squamata",
                                        "family": "Colubridae",
                                        "genus": "Pantherophis",
                                        "image_url": "https://www.vtherpatlas.org/wp2016/wp-content/uploads/2017/08/P.-alleghaniensis-1-Nick-Arms.jpg",
                                        "breeding": "Below Average",
                                        "region": "North America",
                                        "holdings": {},
                                            },

                                        "Red-Eyed Devil Katydid": {
                                        "common": "Red-Eyed Devil Katydid",
                                        "scientific": "Neobarrettia spinosa",
                                        "info": "A species of katyidd native to the arid lands of the United States and Mexico, the red-eyed devil is a notorious predator of small animals. It is not picky and has been known to eat everything from insects to birds. Their bite is strong and allows them to pin prey in place.",
                                        "type": "Invertebrate",
                                        "order": "Orthoptera",
                                        "family": "Tettigoniidae",
                                        "genus": "Neobarrettia",
                                        "image_url": "https://static.inaturalist.org/photos/2267006/large.jpg",
                                        "breeding": "Below Average",
                                        "region": "North America",
                                        "holdings": {},
                                            },

                                        "Eastern Screech Owl": {
                                        "common": "Eastern Screech Owl",
                                        "scientific": "Megascops asio",
                                        "info": "The eastern screech owl is a small owl relatively common across its range in eastern and central North America. Their name comes from their screeching call, which was considered unnerving by early settlers of the region. They feed on a wide variety of small animals, preferring insects and small mammals.",
                                        "type": "Bird",
                                        "order": "Strigiformes",
                                        "family": "Strigidae",
                                        "genus": "Megascops",
                                        "image_url": "https://i.imgur.com/svVc1xN.jpeg",
                                        "breeding": "Below Average",
                                        "region": "North America",
                                        "holdings": {},
                                        },
                                        "Brown Rat": {
                                        "common": "Brown Rat",
                                        "scientific": "Rattus norvegicus",
                                        "info": "The most widespread and common rat species, the brown rat can be found, with few exceptions, everywhere that humans live. Thought to have originated in northern China and nearby areas, the brown rat's adaptability and hardiness has made it a cosmopolitan species.",
                                        "type": "Mammal",
                                        "order": "Rodentia",
                                        "family": "Muridae",
                                        "genus": "Rattus",
                                        "image_url": "https://i.imgur.com/qg1zRI9.jpeg",
                                        "breeding": "Very Easy",
                                        "region": "North America, South America, Europe, Asia, Africa",
                                        "holdings": {},
                                        },
                                        "Great Gray Owl": {
                                        "common": "Great Gray Owl",
                                        "scientific": "Strix nebulosa",
                                        "info": "The longest owl by length, the great gray owl can be found across the Northern Hemisphere from North America to Asia. A powerful apex predator, the great gray owl feeds mostly upon rodents, but will take other animals as well on occasion. They are difficult to find in situ, unusual for such a high-ranking predator.",
                                        "type": "Bird",
                                        "order": "Strigiformes",
                                        "family": "Strigidae",
                                        "genus": "Strix",
                                            "images": [
                                                {"label": "Eurasian great gray owl (lapponica)", "url": "https://i.imgur.com/uOdw3m3.jpeg"}
                                            ],
                                        "breeding": "Below Average",
                                        "region": "North America, Europe, Asia,",
                                        "holdings": {},
                                        },
                                        "Common Buzzard": {
                                        "common": "Common Buzzard",
                                        "scientific": "Buteo buteo",
                                        "info": "A well-known opportunistic predator, the common buzzard can be found throughout the Old World. They are devoted parents to their young, building large nests for them. They are considered one of the most common birds of prey in the world, with population estimates running into the millions.",
                                        "type": "Bird",
                                        "order": "Accipitriformes",
                                        "family": "Accipitridae",
                                        "genus": "Buteo",
                                            "images": [
                                                {"label": "Central European buzzard (buteo)", "url": "https://i.imgur.com/lE9Cq7c.jpeg"}
                                            ],
                                        "breeding": "Average",
                                        "region": "Europe, Asia, Africa",
                                        "holdings": {},
                                        },
                                        "Reticulated Python": {
                                        "common": "Reticulated Python",
                                        "scientific": "Malayopython reticulatus",
                                        "info": "The longest of all snakes and the third-heaviest in the world, the reticulated python is an apex predator in its native range of southeast Asia. They are well-adapted to living in human-disturbed areas, being somewhat common in places such as Bangkok. They are occasionally dangerous to humans and should be respected when nearby.",
                                        "type": "Reptile",
                                        "order": "Squamata",
                                        "family": "Pythonidae",
                                        "genus": "Malayopython",
                                        "image_url": "https://i.imgur.com/TDLfid9.jpeg",
                                        "breeding": "Average",
                                        "region": "Asia",
                                        "holdings": {},
                                        },
                                        "Brown Wood Owl": {
                                        "common": "Brown Wood Owl",
                                        "scientific": "Strix leptogrammica",
                                        "info": "An owl species native to south, southeast, and east Asia, the brown wood owl is a nocturnal species that prefers densely forested areas, per its name. They mainly feed on small mammals, birds, and reptiles, and have 14 recognized subspecies distributed across their range.",
                                        "type": "Bird",
                                        "order": "Strigiformes",
                                        "family": "Strigidae",
                                        "genus": "Strix",
                                        "image_url": "https://i.imgur.com/BGS3FMT.jpeg",
                                        "breeding": "Below Average",
                                        "region": "Asia",
                                        "holdings": {},
                                        },
                                        "Giant Otter": {
                                        "common": "Giant Otter",
                                        "scientific": "Pteronura brasiliensis",
                                        "info": "One of the largest otter species in the world, the giant otter resides in the Amazon basin and the Pantanal of South America. A pack-hunting apex predator, they are specialized hunters of fish and will occasionally take crabs and reptiles as well. They are unfortunately endangered due to poaching and habitat loss.",
                                        "type": "Mammal",
                                        "order": "Carnivora",
                                        "family": "Mustelidae",
                                        "genus": "Pteronura",
                                        "image_url": "https://i.imgur.com/9sJDhiH.jpeg",
                                        "breeding": "Difficult",
                                        "region": "South America",
                                        "holdings": {},
                                        },
                                        "Blackback Land Crab": {
                                        "common": "Blackback Land Crab",
                                        "scientific": "Gecarcinus lateralis",
                                        "info": "One of many land crabs native to the Caribbean region, the blackback land crab gets its common name from the black patch of its carapace, which helps to identify it from similar species. There are many color variants but they all retain this distinctive patterning.",
                                        "type": "Invertebrate",
                                        "order": "Decapoda",
                                        "family": "Gecarcinidae",
                                        "genus": "Gecarcinus",
                                        "image_url": "https://i.imgur.com/OPZTzNm.jpeg",
                                        "breeding": "Impossible",
                                        "region": "North America, South America",
                                        "holdings": {},
                                        },
    "Common Moon Jelly": {
        "common": "Common Moon Jelly",
        "scientific": "Aurelia aurita",
        "info": "Probably the most common jellyfish species in the world, the common moon jelly is found circumglobally and is often displayed in public aquariums. It is a micropredator, feeding on small animals in the water column with its mild venom, which can be painful to humans.",
        "type": "Invertebrate",
        "order": "Semaeostomeae",
        "family": "Ulmaridae",
        "genus": "Aurelia",
        "image_url": "https://i.imgur.com/h9leK9t.jpeg",
        "breeding": "Difficult",
        "region": "North America, South America, Europe, Asia, Africa, Oceania, Antarctica",
        "holdings": {
            "Europe": "8 - Blue Seas Aquarium"
        }
                                        },
                                        "Textile Cone": {
                                        "common": "Textile Cone",
                                        "scientific": "Conus textile",
                                        "info": "An extremely venomous species of sea snail, the textile cone can be found throughout the Indo-Pacific. Its attractive shell coloration is sought-after by shell collectors, This species is typically found in shallow areas buried in the sand, emerging at night to feed on sea snails.",
                                        "type": "Invertebrate",
                                        "order": "Neogastropoda",
                                        "family": "Conidae",
                                        "genus": "Conus",
                                        "image_url": "https://i.imgur.com/lpsjkBt.jpeg",
                                        "breeding": "Impossible",
                                        "region": "Asia, Africa, Oceania",
                                        "holdings": {},
                                        },
                                        "European Medicinal Leech": {
                                        "common": "European Medicinal Leech",
                                        "scientific": "Hirudo medicinalis",
                                        "info": "One of several species of leeches formerly used in the medical field, the European medicinal leech has suffered population declines in its native habitat due to overcollection for this trade. Their habitat of muddy pools and ponds with plentiful vegetation have also been reduced, further endangering the species.",
                                        "type": "Invertebrate",
                                        "order": "Arhynchobdellida",
                                        "family": "Hirudinidae",
                                        "genus": "Hirudo",
                                        "image_url": "https://i0.wp.com/adlayasanimals.wordpress.com/wp-content/uploads/2021/02/hirudo_medicinalis.jpg?fit=1200%2C820&ssl=1",
                                        "breeding": "Impossible",
                                        "region": "Europe, Asia",
                                        "holdings": {},
                                        },
                                        "Racovitza's Rudd": {
                                        "common": "Racovitza's Rudd",
                                        "scientific": "Scardinius racovitzai",
                                        "info": "A freshwater fish closely related to the common rudd, the Racotviza's rudd was formerly endemic to Romania. Now extinct in the wild, this species survives on due to zoos breeding the species in captivity. It was found in one lake natively, until the lake dried up in 2014.",
                                        "type": "Fish",
                                        "order": "Cypriniformes",
                                        "family": "Leuciscidae",
                                        "genus": "Scardinius",
                                        "image_url": "https://i.imgur.com/xpybKED.jpeg",
                                        "breeding": "Difficult",
                                        "region": "Europe",
                                        "holdings": {},
                                        },
                                        "Eurasian Beaver": {
                                        "common": "Eurasian Beaver",
                                        "scientific": "Castor fiber",
                                        "info": "One of the two extant species of beavers, the Eurasian beaver can be distinguished from the North American beaver by its slightly longer skull. They number 1.5 million individuals in situ, and their population is rapidly increasing.",
                                        "type": "Mammal",
                                        "order": "Rodentia",
                                        "family": "Castoridae",
                                        "genus": "Castor",
                                        "image_url": "https://i.imgur.com/aB0JRFW.jpeg",
                                        "breeding": "Average",
                                        "region": "Europe, Asia",
                                        "holdings": {},
                                        },
                                        "European Badger": {
                                            "common": "European Badger",
                                            "scientific": "Meles meles",
                                            "info": "A large mustelid native to Europe and western Asia, the European badger is well-known for eating just about anything and its tolerance of human-inhabited areas, though not to the extent of fellow small carnivores like red foxes. They are generally common and increasing in population due to a reduction in rabies.",
                                            "type": "Mammal",
                                            "order": "Carnivora",
                                            "family": "Mustelidae",
                                            "genus": "Meles",
                                            "images": [
                                                {"label": "Common badger (meles)", "url": "https://i.imgur.com/UuEFIBW.jpeg"}
                                            ],
                                            "breeding": "Below Average",
                                            "region": "Europe, Asia",
                                            "holdings": {},
                                            },
                                            "Eurasian Eagle-Owl": {
                                                "common": "Eurasian Eagle-Owl",
                                                "scientific": "Bubo bubo",
                                                "info": "A large, powerfully-built owl native to wide swathes of Europe and Asia, the Eurasian eagle-owl is a nocturnal apex predator, taking a wide variety of prey, with a majority being small mammals. They are a long-lived species, living up to 27 years in the wild and more in captivity.",
                                                "type": "Bird",
                                                "order": "Strigiformes",
                                                "family": "Strigidae",
                                                "genus": "Bubo",
                                                "images": [
                                                    {"label": "European eagle-owl (bubo)", "url": "https://i.imgur.com/gSPtnvU.jpeg"}
                                                ],
                                                "breeding": "Below Average",
                                                "region": "Europe, Asia",
                                                "holdings": {
                                                    "Europe": "0.1 - Brussels Zoo", },
},
                                                "Long-Eared Owl": {
                                                    "common": "Long-Eared Owl",
                                                    "scientific": "Asio otus",
                                                    "info": "This medium-sized owl species can be found in the Palearctic realm. With a huge range and relative adaptability, this species has a population estimated from 500,000 to 5 million individuals. They typically prefer semi-open habitats, such as the edge of woodlands.",
                                                    "type": "Bird",
                                                    "order": "Strigiformes",
                                                    "family": "Strigidae",
                                                    "genus": "Asio",
                                                    "images": [
                                                        {"label": "Eurasian long-eared owl (otus)", "url": "https://i.imgur.com/EPVt2Bt.jpeg"}
                                                    ],
                                                    "breeding": "Below Average",
                                                    "region": "North America, Europe, Asia, Africa",
                                                    "holdings": {},
                                                    },
                                                    "Western Barn Owl": {
                                                        "common": "Western Barn Owl",
                                                        "scientific": "Tyto alba",
                                                        "info": "One of the most well-known owl species, the western barn owl gets its name from its propensity to roost in barns, which have plentiful rodents to hunt. Formerly one wide-ranging taxa, the barn owl was split into several species, with the western barn owl residing in western and central Europe, west Asia, Africa, and various Atlantic/Indian Ocean islands.",
                                                        "type": "Bird",
                                                        "order": "Strigiformes",
                                                        "family": "Tytonidae",
                                                        "genus": "Tyto",
                                                        "images": [
                                                            {"label": "Western barn owl (alba)", "url": "https://i.imgur.com/i7Hsr9o.jpeg"}
                                                        ],
                                                        "breeding": "Below Average",
                                                        "region": "Europe, Asia, Africa",
                                                        "holdings": {},
                                                        },
                                                        "Great Crested Newt": {
                                                            "common": "Great Crested Newt",
                                                            "scientific": "Triturus cristatus",
                                                            "info": "A large newt growing up to 6.3 inches in length, the great crested newt is found throughout temperate Europe, from Great Britain to Russia. They have a well-studied life cycle, going from eggs to tadpoles to juveniles known as efts, to adults who alternate between land and water. Males develop the well-known crests during the breeding season.",
                                                            "type": "Amphibian",
                                                            "order": "Urodela",
                                                            "family": "Salamandridae",
                                                            "genus": "Triturus",
                                                            "image_url": "https://i.imgur.com/YQqfnmn.jpeg",
                                                            "breeding": "Average",
                                                            "region": "Europe",
                                                            "holdings": {},
                                                            },
                                                            "Radiated Wolf Spider": {
                                                                "common": "Radiated Wolf Spider",
                                                                "scientific": "Hogna radiata",
                                                                "info": "A wandering wolf spider find throughout Europe, Asia, and Africa. They typically prefer warm, dry habitat and as such is abundant in the Mediterranean region, being one of the most commonly sighted wolf spiders there.",
                                                                "type": "Invertebrate",
                                                                "order": "Araneae",
                                                                "family": "Lycosidae",
                                                                "genus": "Hogna",
                                                                "images": [
                                                                    {"label": "Italian radiated wolf spider (minor)", "url": "https://i.imgur.com/bxsH2SF.jpeg"}
                                                                ],
                                                                "breeding": "Average",
                                                                "region": "Europe, Asia, Africa",
                                                                "holdings": {},
                                                        },
                                                        "Barred Owl": {
                                                            "common": "Barred Owl",
                                                            "scientific": "Strix varia",
                                                            "info": "A large owl species ranging across much of North America, the barred owl has a distinctive call that sounds like the words 'who-cooks-for-you-all?'. Preferring mature woodlands but somewhat adaptable, the barred owl has been encroaching westward, endangering the related spotted owl.",
                                                            "type": "Bird",
                                                            "order": "Strigiformes",
                                                            "family": "Strigidae",
                                                            "genus": "Strix",
                                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/359094470/large.jpg",
                                                            "breeding": "Below Average",
                                                            "region": "North America",
                                                            "holdings": {},
                                                            },
                                                            "European Mouflon": {
                                                                "common": "European Mouflon",
                                                                "scientific": "Ovis aries musimon",
                                                                "info": "A feral subspecies of domestic sheep, the European mouflon is an interesting case in feral species, as it seems to have become feral before domestication fully took place. As such, the European mouflon is more adapted to mountainous environments, just like its ancestor the mouflon.",
                                                                "type": "Mammal",
                                                                "order": "Artiodactyla",
                                                                "family": "Bovidae",
                                                                "genus": "Ovis",
                                                                "image_url": "https://static.inaturalist.org/photos/45828139/large.jpeg",
                                                                "breeding": "Average",
                                                                },
                                                            "Wild Turkey": {
                                                                "common": "Wild Turkey",
                                                                "scientific": "Meleagris gallopavo ",
                                                                "info": "A large gamebird native to North America, the wild turkey is culturally important to the people of this continent. Common in forests but somewhat adaptable to human-inhabited areas, male turkeys are known for their elaborate feather displays towards the females, which includes the distinctive snood on their snout.",
                                                                "type": "Bird",
                                                                "order": "Galliformes",
                                                                "family": "Phasianidae",
                                                                "genus": "Meleagris",
                                                                "images": [
                                                                    {"label": "Merriam's wild turkey (merriami)", "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/188573264/original.jpg"}
                                                                ],
                                                                "breeding": "Average",
                                                                "region": "North America",
                                                                "holdings": {},
                                                                },
                                                                "Mule Deer": {
                                                                "common": "Mule Deer",
                                                                "scientific": "Odocoileus hemionus",
                                                                "info": "One of the two most common deer species in North America, the mule deer is restricted to habitat west of the Rocky Mountains. Adaptable and common, several subspecies exist spread across its range. The name 'mule deer' comes from the ears, which look similar to that of a mule.",
                                                                "type": "Mammal",
                                                                "order": "Artiodactyla",
                                                                "family": "Cervidae",
                                                                "genus": "Odocoileus",
                                                                "image_url": "https://i.imgur.com/iSzuP9V.jpeg",
                                                                "breeding": "Average",
                                                                "region": "North America",
                                                                "holdings": {},
                                                            },
                                                            "Elk": {
                                                            "common": "Elk",
                                                            "scientific": "Cervus canadensis ",
                                                            "info": "A large deer native to Asia and North America, the elk has several names that it is referred to by, including wapiti (which has become more common in recent years). These deer are well known for their bugling cries, which can be heard from great distances.",
                                                            "type": "Mammal",
                                                            "order": "Artiodactyla",
                                                            "family": "Cervidae",
                                                            "genus": "Cervus",
                                                            "images": [
                                                                    {"label": "Rocky Mountain elk (nelsoni)", "url": "https://i.imgur.com/PBErPGm.jpeg"}
                                                                ],
                                                            "breeding": "Average",
                                                            "region": "North America, Asia",
                                                            "holdings": {},
                                                            },
                                                            "Sockeye Salmon": {
                                                            "common": "Sockeye Salmon",
                                                            "scientific": "Oncorhynchus nerka ",
                                                            "info": "Probably the most well-known of the Pacific salmon, the sockeye salmon is known for its dramatic migrations from the river to the sea and back again for spawning. They are an important food source for many animals and are a keystone species because of this.",
                                                            "type": "Fish",
                                                            "order": "Salmoniformes",
                                                            "family": "Salmonidae",
                                                            "genus": "Oncorhynchus",
                                                            "images": [
                                                                    {"label": "Kokanee salmon", "url": "https://www.joelsartore.com/wp-content/uploads/stock/FIS001/FIS001-00084.jpg"}
                                                                ],
                                                            "breeding": "Impossible",
                                                            "region": "North America, Asia",
                                                            "holdings": {},
                                                            },
                                                            "Magenta Dottyback": {
                                                            "common": "Magenta Dottyback",
                                                            "scientific": "Pictichromis porphyrea ",
                                                            "info": "A dottyback species found in the Indo-Pacific region from the Philippines to the central Pacific, the magenta dottyback is commonly traded in the aquarium trade. It is a boisterous and aggressive species that defends its territory fiercely.",
                                                            "type": "Fish",
                                                            "order": "Blenniiformes",
                                                            "family": "Pseudochromidae",
                                                            "genus": "Pictichromis",
                                                            "image_url": "https://i.imgur.com/ljQM1W8.jpeg",
                                                            "breeding": "Impossible",
                                                            "region": "Asia, Oceania",
                                                            "holdings": {},
                                                            },
                                                            "Azure Damselfish": {
                                                            "common": "Azure Damselfish",
                                                            "scientific": "Chrysiptera hemicyanea ",
                                                            "info": "Unlike many damselfish, the azure damselfish is rather peaceful and will often live in large, loosely associated schools to protect themselves from predators. They are exceedingly popular in the aquarium trade for their hardiness and peaceful nature.",
                                                            "type": "Fish",
                                                            "order": "Blenniiformes",
                                                            "family": "Pomacentridae",
                                                            "genus": "Chrysiptera",
                                                            "image_url": "https://i.imgur.com/nVUh36X.jpeg",
                                                            "breeding": "Impossible",
                                                            "region": "Asia, Oceania",
                                                            "holdings": {},
                                                            },
                                                            "Yellowtail Damselfish": {
                                                            "common": "Yellowtail Damselfish",
                                                            "scientific": "Chrysiptera parasema ",
                                                            "info": "A small damselfish found in the Indo-Pacific, the yellowtail damselfish is popular in the aquarium trade due to its hardiness and peaceful nature compared to most damselfish. They are able to change color depending on their mood, with a darker color indicating stress.",
                                                            "type": "Fish",
                                                            "order": "Blenniiformes",
                                                            "family": "Pomacentridae",
                                                            "genus": "Chrysiptera",
                                                            "image_url": "https://i.imgur.com/Ww1TuFj.jpeg",
                                                            "breeding": "Impossible",
                                                            "region": "Asia, Oceania",
                                                            "holdings": {},
                                                        },
                                                        "Common Leopard Gecko": {
                                                        "common": "Common Leopard Gecko",
                                                        "scientific": "Eublepharis macularius ",
                                                        "info": "One of the most popular pet lizards, the common leopard gecko is native originally to west and south Asia's arid environments. It is an opportunistic carnivore, feeding mainly on insects. Their hardiness and docile nature makes them one of the most popular reptiles kept in captivity.",
                                                        "type": "Reptile",
                                                        "order": "Squamata",
                                                        "family": "Eublepharidae",
                                                        "genus": "Eublepharis",
                                                        "image_url": "https://i.imgur.com/JycYDcG.jpeg",
                                                        "breeding": "Easy",
                                                        "region": "Asia",
                                                        "holdings": {
                                                            "North America": "1.0 - Essex County Zoo", },
                                                    },
                                                    "Central Bearded Dragon": {
                                                    "common": "Central Bearded Dragon",
                                                    "scientific": "Pogona vitticeps ",
                                                    "info": "A wide-ranging agamid lizard endemic to Australia, the central bearded dragon can be found in the central and eastern parts of Australia, typically in arid or semi-arid environments. They are popular pet lizards due to their docile nature and hardiness.",
                                                    "type": "Reptile",
                                                    "order": "Squamata",
                                                    "family": "Agamidae",
                                                    "genus": "Pogona",
                                                    "image_url": "https://i.imgur.com/WDxUGd8.jpeg",
                                                    "breeding": "Easy",
                                                    "region": "Oceania",
                                                        "holdings": {
                                                            "North America": "1.1 - Essex County Zoo", },
                                                    },
                                                    "Crested Gecko": {
                                                    "common": "Crested Gecko",
                                                    "scientific": "Correlophus ciliatus",
                                                    "info": "This gecko species is endemic to New Caledonia and was thought to be extinct until its rediscovery in 1994. It has since become a popular pet lizard due to its small size and ease of care, and now has a huge captive population in zoos as a conservation measure.",
                                                    "type": "Reptile",
                                                    "order": "Squamata",
                                                    "family": "Diplodactylidae",
                                                    "genus": "Correlophus",
                                                    "image_url": "https://www.pierrewildlife.com/wp-content/uploads/2024/06/Correlophus-cristatus.jpg",
                                                    "breeding": "Easy",
                                                    "region": "Oceania",
                                                        "holdings": {
                                                            "North America": "1.0 - Essex County Zoo", },
                                                    },
                                                    "Big Brown Bat": {
                                                    "common": "Big Brown Bat",
                                                    "scientific": "Eptesicus fuscus",
                                                    "info": "A relatively large species of bat native to the Americas, the big brown bat is a voracious insectivore, so much so that they are considered beneficial for farmers and agriculturalists. Bat boxes, which are artifical roosting sites, have helped the species adapt to human-inhabited areas.",
                                                    "type": "Mammal",
                                                    "order": "Chiroptera",
                                                    "family": "Vespertilionidae",
                                                    "genus": "Eptesicus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/11370649/large.jpg",
                                                    "breeding": "Difficult",
                                                    "region": "North America, South America",
                                                    "holdings": {},
                                                    },
                                                    "American Crow": {
                                                    "common": "American Crow",
                                                    "scientific": "Corvus brachyrhynchos",
                                                    "info": "A common corvid found throughout North America, the American crow is an adaptable omnivore, feeding on a variety of plant and animal matter. They are considered one of the most intelligent bird species, able to use tools and plan feeding strategies.",
                                                    "type": "Bird",
                                                    "order": "Passeriformes",
                                                    "family": "Corvidae",
                                                    "genus": "Corvus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/97752362/large.jpg",
                                                    "breeding": "Average",
                                                    "region": "North America",
                                                        "holdings": {
                                                            "North America": "1.1 - Karl Eberhard's Pine Lake Zoo", },
                                                    },
                                                    "Western Black Widow": {
                                                    "common": "Western Black Widow",
                                                    "scientific": "Latrodectus hesperus",
                                                    "info": "This venomous spider species can be found throughout the western regions of North America. Infamous for its potent venom, the western black widow is actually a shy species with little propensity for biting humans; only a handful of people have ever died from western black widow bites.",
                                                    "type": "Invertebrate",
                                                    "order": "Araneae",
                                                    "family": "Theridiidae",
                                                    "genus": "Latrodectus",
                                                    "image_url": "https://i.imgur.com/G9jV2w9.jpeg",
                                                    "breeding": "Below Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                    },
                                                    "Zebra Isopod": {
                                                        "common": "Zebra Isopod",
                                                        "scientific": "Armadillidium maculatum",
                                                        "info": "Endemic to a small area of southeastern France, the zebra isopod has become an immensely popular species in the invertebrate hobby. It is a hardy species that like all isopods requires some ambient moisture to thrive.",
                                                        "type": "Invertebrate",
                                                        "order": "Isopoda",
                                                        "family": "Armadillidiidae",
                                                        "genus": "Armadillidium",
                                                        "images": [
                                                            {"label": "Wild type", "url": "https://frogdaddy.net/cdn/shop/products/IMG_4287.jpg?v=1609730804&width=1810"},
                                                            {"label": "Champagne morph", "url": "https://www.petpedesandpods.com/wp-content/uploads/2022/03/Photo_1667080910280-scaled.jpg"},
                                                            {"label": "Yellow zebra morph", "url": "https://richardsinverts-store.com/cdn/shop/products/zebra-isopod-yellow-armadillidium-maculatum-yellow-448608.jpg?v=1674907343&width=1445"},
                                                        ],
                                                        "image_url": "https://static.inaturalist.org/photos/4465554/large.png",
                                                        "breeding": "Very Easy",
                                                        "region": "Europe",
                                                        "holdings": {},
                                                    },
                                                    "Domestic Cow": {
                                                        "common": "Domestic Cow",
                                                        "scientific": "Bos taurus",
                                                        "info": "One of the most common domesticated animals, domestic cows are the descendants of the now extinct aurochs of Eurasia. Domestic cattle are often used for meat and milk, but sometimes are kept as pets. There are over 1,000 recognized breeds of cow.",
                                                        "type": "Mammal",
                                                        "order": "Artiodactyla",
                                                        "family": "Bovidae",
                                                        "genus": "Bos",
                                                        "images": [
                                                            {"label": "Florida Cracker Cow", "url": "https://i.imgur.com/w9Mpk73.jpeg"},
                                                            {"label": "Ankole-Watusi", "url": "https://i.imgur.com/eC2OAKt.jpeg"},
                                                        ],
                                                        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/159472/large.jpg",
                                                        "breeding": "Easy",
                                                    },
                                                    "African Arowana": {
                                                        "common": "African Arowana",
                                                        "scientific": "Heterotis niloticus",
                                                        "info": "Despite being called an arowana, the African arowana is actually more closely related to the arapaimas of South America, which is conspicuous in its morphology. Like many bonytongue fish they are able to breathe air with primitive lung-like organs, giving them an advantage in oxygen-poor water.",
                                                        "type": "Fish",
                                                        "order": "Osteoglossiformes",
                                                        "family": "Arapaimidae",
                                                        "genus": "Heterotis",
                                                        "image_url": "https://i.imgur.com/z9CuoNf.jpeg",
                                                        "breeding": "Impossible",
                                                        "region": "Africa",
                                                        "holdings": {},
                                                    },
                                                    "Plains Zebra": {
                                                        "common": "Plains Zebra",
                                                        "scientific": "Equus quagga",
                                                        "info": "Of all animals, the plains zebra is amongst the most famous due to its distinctive coloration and resemblance to domestic horses. Native to parts of east and southern Africa, the plains zebra is divided into six subspecies, including the extinct quagga.",
                                                        "type": "Mammal",
                                                        "order": "Perissodactyla",
                                                        "family": "Equidae",
                                                        "genus": "Equus",
                                                        "images": [
                                                            {"label": "Burchell's zebra (burchellii)", "url": "https://i.imgur.com/RBsflau.jpeg"},
                                                        ],
                                                        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Equus_quagga_burchellii_-_Etosha%2C_2014.jpg/1280px-Equus_quagga_burchellii_-_Etosha%2C_2014.jpg",
                                                        "breeding": "Average",
                                                        "region": "Africa",
                                                        "holdings": {},
                                                    },
                                                    "Striped Burrfish": {
                                                        "common": "Striped Burrfish",
                                                        "scientific": "Chilomycterus schoepfii",
                                                        "info": "Found in the tropical and temperate waters of the western Atlantic Ocean, the striped burrfish is adaptable and can be found in salt and brackish water. They are found in shallow water no deeper than 11m and like all porcupinefish feed primarily on crustaceans and other invertebrates.",
                                                        "type": "Fish",
                                                        "order": "Tetraodontiformes",
                                                        "family": "Diodontidae",
                                                        "genus": "Chilomycterus",
                                                        "image_url": "https://aqua.org/assets/animals/_open_graph_1x/70174/2020-04-01_animal_striped-burrfish_center-center_001.webp",
                                                        "breeding": "Impossible",
                                                        "region": "North America, South America",
                                                        "holdings": {},
                                                },
                                                "Mummichog": {
                                                    "common": "Mummichog",
                                                    "scientific": "Fundulus heteroclitus",
                                                    "info": "One of the largest killifish species, the mummichog inhabits brackish water ecosystems in eastern North America, but has also been introduced to Iberia as well. Unusually for a killifish, they are able to breathe air.",
                                                    "type": "Fish",
                                                    "order": "Cyprinodontiformes",
                                                    "family": "Fundulidae",
                                                    "genus": "Fundulus",
                                                    "image_url": "https://i.imgur.com/pgn24JS.jpeg",
                                                    "breeding": "Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Banded Killifish": {
                                                    "common": "Banded Killifish",
                                                    "scientific": "Fundulus diaphanus",
                                                    "info": "The banded killifish is the only freshwater killifish that inhabits eastern North America, it can also occasionally be found in brackish water as well. They are sometimes found in the killifish hobby due to their hardiness and attractive coloration.",
                                                    "type": "Fish",
                                                    "order": "Cyprinodontiformes",
                                                    "family": "Fundulidae",
                                                    "genus": "Fundulus",
                                                    "image_url": "https://lh4.googleusercontent.com/proxy/lyZoiFAss2Lf93ynhK2QAT4pXamktBe7o5d5cGyupXF9MeUxhWCGo1QFAvL7iKl9T9C0oRFivPE88qSraYO1QWwDxuF-XHftVf0C",
                                                    "breeding": "Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                    },
                                                    "Eastern Mudsnail": {
                                                        "common": "Eastern Mudsnail",
                                                        "scientific": "Ilyanassa obsoleta",
                                                        "info": "A small snail that can be found in brackish and salt water, the eastern mudsnail is abundant in its range, feeding on biofilm resting on the sand. It is natively found in the temperate eastern part of North America.",
                                                        "type": "Invertebrate",
                                                        "order": "Neogastropoda",
                                                        "family": "Nassariidae",
                                                        "genus": "Ilyanassa",
                                                        "image_url": "https://www.exoticsguide.org/sites/default/files/species_images/i_obsoleta_lg_b.jpg",
                                                        "breeding": "Impossible",
                                                        "region": "North America",
                                                        "holdings": {},
                                                        },
                                                        "Atlantic Blue Crab": {
                                                            "common": "Atlantic Blue Crab",
                                                            "scientific": "Callinectes sapidus",
                                                            "info": "A distinctive crab found in brackish and intertidal areas along the eastern coasts of North and South America, the Atlantic blue crab is a keystone species in its native range, serving as a predator of smaller invertebrates and an invaluable food source for larger fish.",
                                                            "type": "Invertebrate",
                                                            "order": "Decapoda",
                                                            "family": "Portunidae",
                                                            "genus": "Callinectes",
                                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/134165478/original.jpg",
                                                            "breeding": "Impossible",
                                                            "region": "North America, South America",
                                                            "holdings": {},
                                                            },
                                                            "Portly Spider Crab": {
                                                                "common": "Portly Spider Crab",
                                                                "scientific": "Libinia emarginata",
                                                                "info": "One of the most abundant crabs in the northwestern Atlantic, the portly spider crab can be found from brackish estuaries to moderately deep depths of 49m. Their main diet consists of large starfish.",
                                                                "type": "Invertebrate",
                                                                "order": "Decapoda",
                                                                "family": "Epialtidae",
                                                                "genus": "Libinia",
                                                                "image_url": "https://mote.org/wp-content/uploads/2024/11/3e851bd7-7fd2-47a1-9399-32036a1f69dc_lg-1024x683.jpg",
                                                                "breeding": "Impossible",
                                                                "region": "North America, South America",
                                                                "holdings": {},
                                                                },
                                                                "Australian Water Dragon": {
                                                                    "common": "Australian Water Dragon",
                                                                    "scientific": "Intellagama lesueurii",
                                                                    "info": "A semi-aquatic agamid lizard found in eastern Australia, the Australian water dragon is an omnivorous species feeding on various small animals, fruits, and flowers. They are able to change the color of their scales to camoflauge, albeit not as fast as some other species.",
                                                                    "type": "Reptile",
                                                                    "order": "Squamata",
                                                                    "family": "Agamidae",
                                                                    "genus": "Intellagama",
                                                                    "image_url": "https://i.imgur.com/fWDncA9.jpeg",
                                                                    "breeding": "Below Average",
                                                                    "region": "Oceania",
                                                                    "holdings": {},
                                                                    },
                                                                    "Emerald Cockroach Wasp": {
                                                                        "common": "Emerald Cockroach Wasp",
                                                                        "scientific": "Ampulex compressa",
                                                                        "info": "A solitary parasitoid wasp that parasitizes cockroaches as the name implies, the emerald cockroch wasp is distributed amongst the tropics and has benefitted from the spread of various household cockroach species.",
                                                                        "type": "Invertebrate",
                                                                        "order": "Hymenoptera",
                                                                        "family": "Ampulicidae",
                                                                        "genus": "Ampulex",
                                                                        "image_url": "https://i.imgur.com/xJKjj4w.jpeg",
                                                                        "breeding": "Difficult",
                                                                        "region": "Asia, Africa, Oceania",
                                                                        "holdings": {},
                                                                    },
                                                                    "Red-Tailed Hawk": {
                                                                        "common": "Red-Tailed Hawk",
                                                                        "scientific": "Buteo jamaicensis",
                                                                        "info": "One of the most common birds of prey in North America, the red-tailed hawk is a popular species for falconry due to its ability to quickly learn commands. They are unfazed by human activity and serve as important control agents for rats and pigeons in urban areas.",
                                                                        "type": "Bird",
                                                                        "order": "Accipitriformes",
                                                                        "family": "Accipitridae",
                                                                        "genus": "Buteo",
                                                                        "image_url": "https://i.imgur.com/cOHq8jd.jpeg",
                                                                        "breeding": "Average",
                                                                        "region": "North America",
                                                                        "holdings": {
                                                                            "North America": "0.1 - Karl Eberhard's Pine Lake Zoo", },
                                                                    },
                                                                    "Cooper's Hawk": {
                                                                        "common": "Cooper's Hawk",
                                                                        "scientific": "Astur cooperii",
                                                                        "info": "A medium-sized hawk found in North America, the Cooper's hawk is not as adaptable as some of its cousins, and prefers undisturbed wilderness to roost and hunt in. They are highly agile birds and can hunt prey larger than themselves with ease due to this.",
                                                                        "type": "Bird",
                                                                        "order": "Accipitriformes",
                                                                        "family": "Accipitridae",
                                                                        "genus": "Astur",
                                                                        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/170370008/original.jpg",
                                                                        "breeding": "Average",
                                                                        "region": "North America",
                                                                        "holdings": {},
                                                                    },
                                                                    "Western Tiger Swallowtail": {
                                                                        "common": "Western Tiger Swallowtail",
                                                                        "scientific": "Papilio rutulus",
                                                                        "info": "The western tiger swallowtail is endemic to western North America, from British Columbia to Texas. They are a larger swallowtail species with a wingspan of 3-4 inches. Its pupae are heavily resistant to cold winters, a necessity in the northern parts of its range.",
                                                                        "type": "Invertebrate",
                                                                        "order": "Lepidoptera",
                                                                        "family": "Papilionidae",
                                                                        "genus": "Papilio",
                                                                        "image_url": "https://i.imgur.com/VKlCpJG.jpeg",
                                                                        "breeding": "Average",
                                                                        "region": "North America",
                                                                        "holdings": {},
                                                                    },
                                                                    "Orange Sulphur": {
                                                                        "common": "Orange Sulphur",
                                                                        "scientific": "Colias eurytheme",
                                                                        "info": "The orange sulphur is a widespread North American butterfly ranging from southern Canada to Mexico. Its caterpillars are nocturnal and feed on plants from the family Fabaceae. Occasionally it breeds extensively, causing problems for alfalfa farmers.",
                                                                        "type": "Invertebrate",
                                                                        "order": "Lepidoptera",
                                                                        "family": "Pieridae",
                                                                        "genus": "Colias",
                                                                        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/27553205/original.jpeg",
                                                                        "breeding": "Average",
                                                                        "region": "North America",
                                                                        "holdings": {},
                                                            },
                                                            "Red Admiral": {
                                                                "common": "Red Admiral",
                                                                "scientific": "Vanessa atalanta",
                                                                "info": "One of the most widespread and well-known butterflies in the Palearctic realm, the red admiral prefers moist woodland environments, its caterpillars hosting on nettle plants. A criteria for females selecting males is flight ability; the best-flying males are more likely to successfully court females.",
                                                                "type": "Invertebrate",
                                                                "order": "Lepidoptera",
                                                                "family": "Nymphalidae",
                                                                "genus": "Vanessa",
                                                                "image_url": "https://i.imgur.com/5G6ghFt.jpeg",
                                                                "breeding": "Average",
                                                                "region": "North America, Europe, Asia, Africa",
                                                                "holdings": {},
                                                            },
                                                            "Northern Crescent": {
                                                                "common": "Northern Crescent",
                                                                "scientific": "Phyciodes cocyta",
                                                                "info": "This small butterfly can typically be seen in its adult form from June to July depending on the location. The larvae feed on Asteraceae sp. plants, while the adults feed on nectar. Its range stretches from northern Canada to southern New Mexico.",
                                                                "type": "Invertebrate",
                                                                "order": "Lepidoptera",
                                                                "family": "Nymphalidae",
                                                                "genus": "Phyciodes",
                                                                "image_url": "https://i.imgur.com/m33BpQq.jpeg",
                                                                "breeding": "Average",
                                                                "region": "North America",
                                                                "holdings": {},
                                                            },
                                                            "Greylag Goose": {
                                                                "common": "Greylag Goose",
                                                                "scientific": "Anser anser",
                                                                "info": "The type species of its genus, the greylag goose is the ancestor of most domestic goose breeds, having been domesticated since ancient times. They are distributed across Eurasia and North Africa, from Iceland to South Korea.",
                                                                "type": "Bird",
                                                                "order": "Anseriformes",
                                                                "family": "Anatidae",
                                                                "genus": "Anser",
                                                                "images": [
                                                                    {"label": "Western greylag goose (anser)", "url": "https://i.imgur.com/PBDM8E3.jpeg"},
                                                                ],
                                                                "breeding": "Easy",
                                                                "region": "Europe, Asia, Africa",
                                                                "holdings": {},
                                                            },
                                                            "Mute Swan": {
                                                                "common": "Mute Swan",
                                                                "scientific": "Cygnus olor",
                                                                "info": "The most iconic swan species, the mute swan is native to Eurasia and north Africa. There are 500,000 swans in their native range, with a large percentage concentrated in Russia. They have been introduced to many locations globally, causing negative effects for native species.",
                                                                "type": "Bird",
                                                                "order": "Anseriformes",
                                                                "family": "Anatidae",
                                                                "genus": "Cygnus",
                                                                "image_url": "https://i.imgur.com/6W67wv1.jpeg",
                                                                "breeding": "Average",
                                                                "region": "Europe, Asia, Africa",
                                                                "holdings": {},
                                                            },
                                                            "Tufted Duck": {
                                                                "common": "Tufted Duck",
                                                                "scientific": "Aythya fuligula",
                                                                "info": "A small diving duck with nearly one million wild individuals, the tufted duck is found natively in the Old World from Iceland to the Philippines. A partially migratory species, males and females are very sexually dimorphic, with males having the distinctive tuft of feathers on the back of the head.",
                                                                "type": "Bird",
                                                                "order": "Anseriformes",
                                                                "family": "Anatidae",
                                                                "genus": "Aythya",
                                                                "image_url": "https://i.imgur.com/V6lO4yD.jpeg",
                                                                "breeding": "Average",
                                                                "region": "Europe, Asia, Africa",
                                                                "holdings": {},
                                                            },
                                                            "Hart's Rivulus": {
                                                                "common": "Hart's Rivulus",
                                                                "scientific": "Anablepsoides hartii",
                                                                "info": "Despite being called a rivulus, the Hart's rivulus is actually a member of a closely related genus, Anablepsoides. They are sometimes found in the aquarium trade but they are a niche fish typically only kept by killifish enthusiasts. They are known for their jumping ability.",
                                                                "type": "Fish",
                                                                "order": "Cyprinodontiformes",
                                                                "family": "Rivulidae",
                                                                "genus": "Anablepsoides",
                                                                "image_url": "https://www.itrainsfishes.net/content/species/rivulus_hartii_gr05.jpg",
                                                                "breeding": "Average",
                                                                "region": "South America",
                                                                "holdings": {},
                                                        },
                                                        "Mafia Island Killifish": {
                                                            "common": "Mafia Island Killifish",
                                                            "scientific": "Nothobranchius korthausae",
                                                            "info": "Endemic to Mafia Island in Tanzania, the Mafia Island killifish is one of the most common Nothobranchius species kept in the aquarium trade. They are variable in coloration, with some being bright red and some being bright yellow.",
                                                            "type": "Fish",
                                                            "order": "Cyprinodontiformes",
                                                            "family": "Nothobranchiidae",
                                                            "genus": "Nothobranchius",
                                                            "images": [
                                                                {"label": "Mafia Island yellow locality", "url": "https://static.wixstatic.com/media/4f7ec1_c280d2550f894b9182c2e55f4e5d7057.jpg/v1/fill/w_600,h_360,al_c,q_80,enc_auto/4f7ec1_c280d2550f894b9182c2e55f4e5d7057.jpg"},
                                                            ],
                                                            "breeding": "Average",
                                                            "region": "Africa",
                                                            "holdings": {},
                                                    },
                                                    "Madagascar Giant Hognose Snake": {
                                                        "common": "Madagascar Giant Hognose Snake",
                                                        "scientific": "Leioheterodon madagascariensis",
                                                        "info": "A large snake endemic to Madagascar, the Madagascar giant hognose snake evolved a defensive behavior much like that of cobras, where it will rear up and spread its scales out to form a hood to make itself look bigger. They are not venomous, but possess a paralyzing saliva that makes subduing prey easier.",
                                                        "type": "Reptile",
                                                        "order": "Squamata",
                                                        "family": "Pseudoxyrhophiidae",
                                                        "genus": "Leioheterodon",
                                                        "image_url": "https://static.inaturalist.org/photos/41718069/large.jpg",
                                                        "breeding": "Below Average",
                                                        "region": "Africa",
                                                        "holdings": {},
                                                        },
                                                        "Gray Heron": {
                                                            "common": "Gray Heron",
                                                            "scientific": "Ardea cinerea",
                                                            "info": "A large heron species, the gray heron resides throughout the Old World from islands in the eastern Atlantic all the way to Japan and Indonesia. They are apex predators feeding on a variety of small animals, typically aquatic animals it can ambush.",
                                                            "type": "Bird",
                                                            "order": "Pelecaniformes",
                                                            "family": "Ardeidae",
                                                            "genus": "Ardea",
                                                            "images": [
                                                                {"label": "Eurasian gray heron (cinerea)", "url": "https://i.imgur.com/s6VAY1y.jpeg"},
                                                            ],
                                                            "breeding": "Below Average",
                                                            "region": "Europe, Asia, Africa",
                                                            "holdings": {},
                                                        },
                                                        "Leopard Cat": {
                                                            "common": "Leopard Cat",
                                                            "scientific": "Prionailurus bengalensis",
                                                            "info": "A small cat native to Asia, the leopard cat is a nocturnal and elusive species that typically hunts at night, feeding primarily on small mammals. They were formerly poached in large numbers for their fur but their populations have stabilized and somewhat recovered, though it is still threatened.",
                                                            "type": "Mammal",
                                                            "order": "Carnivora",
                                                            "family": "Felidae",
                                                            "genus": "Prionailurus",
                                                            "image_url": "https://live.staticflickr.com/5639/23228593825_66c649d951_b.jpg",
                                                            "breeding": "Below Average",
                                                            "region": "Asia",
                                                            "holdings": {},
                                                    },
                                                    "Gambel's Quail": {
                                                        "common": "Gambel's Quail",
                                                        "scientific": "Callipepla gambelii",
                                                        "info": "The Gambel's quail is a small ground-dwelling found in the southwestern part of North America. When paired off for mating, they will aggressively defend their nests and selected territories from other pairs. The young are precocial, leaving the nest in just a few hours.",
                                                        "type": "Bird",
                                                        "order": "Galliformes",
                                                        "family": "Odontophoridae",
                                                        "genus": "Callipepla",
                                                        "image_url": "https://i.imgur.com/P0eXG3k.jpeg",
                                                        "breeding": "Average",
                                                        "region": "North America",
                                                        "holdings": {},
                                                        },
                                                        "Toe Biter": {
                                                            "common": "Toe Biter",
                                                            "scientific": "Abedus herberti",
                                                            "info": "The toe biter, also known as the ferocious water bug, is a predatory true bug found throughout the western parts of North America. An ambush predator, they have outsized hunting abilities and have been recorded eating fish and tadpoles.",
                                                            "type": "Invertebrate",
                                                            "order": "Hemiptera",
                                                            "family": "Belostomatidae",
                                                            "genus": "Abedus",
                                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/243949774/original.jpg",
                                                            "breeding": "Below Average",
                                                            "region": "North America",
                                                            "holdings": {},
                                                        },
                                                        "Sunburst Diving Beetle": {
                                                            "common": "Sunburst Diving Beetle",
                                                            "scientific": "Thermonectus marmoratus",
                                                            "info": "A small, colorful aquatic beetle native to North America, the sunburst diving beetle is an adept swimmer that feeds on a variety of small animals, dead or alive. To breathe underwater they take a bubble of oxygen with them, occasionally surfacing for air.",
                                                            "type": "Invertebrate",
                                                            "order": "Coleoptera",
                                                            "family": "Dytiscidae",
                                                            "genus": "Thermonectus",
                                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/205177664/original.jpg",
                                                            "breeding": "Below Average",
                                                            "region": "North America",
                                                            "holdings": {},
                                                        },
                                                        "Impala": {
                                                            "common": "Impala",
                                                            "scientific": "Aepyceros melampus",
                                                            "info": "A medium-sized antelope found in Sub-Saharan Africa, the impala is a common species in its range, and is one of the more well-known African antelopes. Males typically live either by themselves or in bachelor herds, while females live in large herds with their young. They prefer woodland habitat but sometimes can be found on the open savannah.",
                                                            "type": "Mammal",
                                                            "order": "Artiodactyla",
                                                            "family": "Bovidae",
                                                            "genus": "Aepyceros",
                                                            "image_url": "https://i.imgur.com/YMAtLcp.jpeg",
                                                            "breeding": "Average",
                                                            "region": "Africa",
                                                            "holdings": {},
                                                        },
                                                        "Silvered Leaf Monkey": {
                                                            "common": "Silvered Leaf Monkey",
                                                            "scientific": "Trachypithecus cristatus",
                                                            "info": "An Old World monkey found in Indonesia, the silvered leaf monkey gets its name from its distinctive silver fur, though juveniles are orange. They have been known to live in loose aggregations with proboscis monkeys in Borneo.",
                                                            "type": "Mammal",
                                                            "order": "Primates",
                                                            "family": "Cercopithecidae",
                                                            "genus": "Trachypithecus",
                                                            "image_url": "https://static.inaturalist.org/photos/265644533/large.jpg",
                                                            "breeding": "Below Average",
                                                            "region": "Asia",
                                                            "holdings": {},
                                                        },
                                                        "Southern Cassowary": {
                                                            "common": "Southern Cassowary",
                                                            "scientific": "Casuarius casuarius",
                                                            "info": "One of the largest birds in the world, the southern cassowary is regarded as a potentially dangerous species due to its powerful legs and sharp claws. Males typically are the ones to raise the chicks, unusual for birds.",
                                                            "type": "Bird",
                                                            "order": "Casuariiformes",
                                                            "family": "Casuariidae",
                                                            "genus": "Casuarius",
                                                            "image_url": "https://i.imgur.com/veuBuRj.jpeg",
                                                            "breeding": "Below Average",
                                                            "region": "Oceania",
                                                            "holdings": {},
                                                        },
                                                        "Bonefish": {
                                                            "common": "Bonefish",
                                                            "scientific": "Albula vulpes",
                                                            "info": "The bonefish gets its common name from the many small bones within its body. It is endemic to the western Atlantic Ocean. Its feeding behavior is closely tied to the tides, feeding on invertebrates during high tide. It is a valued gamefish and regarded as extremely wary.",
                                                            "type": "Fish",
                                                            "order": "Albuliformes",
                                                            "family": "Albulidae",
                                                            "genus": "Albula",
                                                            "image_url": "https://i.imgur.com/vyjN9OZ.jpeg",
                                                            "breeding": "Impossible",
                                                            "region": "North America, South America",
                                                            "holdings": {},
                                                        },
                                                        "Anderson's Crocodile Newt": {
                                                            "common": "Anderson's Crocodile Newt",
                                                            "scientific": "Echinotriton andersoni",
                                                            "info": "Native to the Ryukyu Islands of Japan and formerly Taiwan, the Anderson's crocodile newt is listed as a vulnerable species. They are threatened by rapid development of the formerly untouched wilderness as well as illegal capture for the pet trade. They are poisonous and secrete the poison through their rib bones.",
                                                            "type": "Amphibian",
                                                            "order": "Urodela",
                                                            "family": "Salamandridae",
                                                            "genus": "Echinotriton",
                                                            "image_url": "https://i.imgur.com/SGxPxAW.jpeg",
                                                            "breeding": "Difficult",
                                                            "region": "Asia",
                                                            "holdings": {},
                                                    },
                                                    "Cameroon Ogre-Faced Spider": {
                                                        "common": "Cameroon Ogre-Faced Spider",
                                                        "scientific": "Asianopis aspectans",
                                                        "info": "This African spider's common name comes from its distinctive face, which makes it a species that is gaining popularity in the pet trade. They can be found throughout central and southern Africa from elevations of 18-840 meters above sea level.",
                                                        "type": "Invertebrate",
                                                        "order": "Araneae",
                                                        "family": "Deinopidae",
                                                        "genus": "Asianopis",
                                                        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/102441182/large.jpg",
                                                        "breeding": "Below Average",
                                                        "region": "Africa",
                                                        "holdings": {},
                                                    },
                                                    "Hispid Cotton Rat": {
                                                        "common": "Hispid Cotton Rat",
                                                        "scientific": "Sigmodon hispidus",
                                                        "info": "Endemic to southern North America, the hispod cotton rat was formerly believed to range across the Americas, but was split recently into 3 distinct species. Preferring grass-dominated habitats, they serve as a keystone species for seed dispersion and food for larger animals.",
                                                        "type": "Mammal",
                                                        "order": "Rodentia",
                                                        "family": "Cricetidae",
                                                        "genus": "Sigmodon",
                                                        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/600318/large.jpg",
                                                        "breeding": "Average",
                                                        "region": "North America",
                                                        "holdings": {},
                                                    },
                                                    "Little Brown Bat": {
                                                        "common": "Little Brown Bat",
                                                        "scientific": "Myotis lucifugus",
                                                        "info": "The little brown bat was formerly highly abundant throughout North America, but a massive population decline caused by the spread of a fungal disease has reduced their populations significantly. They typically consume small flying insects such as flies, beetles, and moths.",
                                                        "type": "Mammal",
                                                        "order": "Chiroptera",
                                                        "family": "Vespertilionidae",
                                                        "genus": "Myotis",
                                                        "image_url": "https://fieldguide.wyndd.org/fgImages/AMACC01010_absfig1_keinath.jpg",
                                                        "breeding": "Difficult",
                                                        "region": "North America",
                                                        "holdings": {},
                                                },
                                                "Oldfield Mouse": {
                                                    "common": "Oldfield Mouse",
                                                    "scientific": "Peromyscus polionotus",
                                                    "info": "A nocturnal deermouse species inhabiting beaches and sandy areas, the oldfield mouse can be split into different subspecies, some of which are highly endangered and have captive breeding programs. They can be found in the southeastern part of the United States from Tennessee to the Gulf Coast.",
                                                    "type": "Mammal",
                                                    "order": "Rodentia",
                                                    "family": "Cricetidae",
                                                    "genus": "Peromyscus",
                                                    "images": [
                                                        {"label": "Perdido Key beach mouse (trissyllepsis)", "url": "https://i.imgur.com/UzGN67l.jpeg"},
                                                    ],
                                                    "breeding": "Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Black Bullhead": {
                                                    "common": "Black Bullhead",
                                                    "scientific": "Ameiurus melas",
                                                    "info": "The black bullhead is a common and very hardy catfish that is natively distributed in central North America. They have been introduced to Europe and have become invasive there, potentially negatively impacting native species.",
                                                    "type": "Fish",
                                                    "order": "Siluriformes",
                                                    "family": "Ictaluridae",
                                                    "genus": "Ameiurus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/366643276/large.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Black Ghost Knifefish": {
                                                    "common": "Black Ghost Knifefish",
                                                    "scientific": "Apteronotus albifrons",
                                                    "info": "The black ghost knifefish gets its common name from a local folk tale that they are actually ghosts of the dead. They are a larger knifefish growing to 20 inches in length, and are weakly electric, using their electricity as a way to see in the murky rivers they live in.",
                                                    "type": "Fish",
                                                    "order": "Gymnotiformes",
                                                    "family": "Apteronotidae",
                                                    "genus": "Apteronotus",
                                                    "image_url": "https://i.imgur.com/C88TduB.jpeg",
                                                    "breeding": "Impossible",
                                                    "region": "South America",
                                                    "holdings": {},
                                                },
                                                "Dungeness Crab": {
                                                    "common": "Dungeness Crab",
                                                    "scientific": "Metacarcinus magister",
                                                    "info": "A medium sized crab with a carapace width of 6-7 inches, the Dungeness crab typically inhabits eelgrass beds and sandy habitats, feeding on a variety of different meaty foods. They are the subject of a large-scale fishery and are prized as food.",
                                                    "type": "Invertebrate",
                                                    "order": "Decapoda",
                                                    "family": "Cancridae",
                                                    "genus": "Metacarcinus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/446850127/large.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Orange Sea Pen": {
                                                    "common": "Orange Sea Pen",
                                                    "scientific": "Ptilosarcus gurneyi",
                                                    "info": "A benthic cnidarian native to the northeastern Pacific Ocean, the orange sea pen is a deepwater species, found from 14 to 225 meters in the ocean. They are filter feeders, extending their tentacles to catch zooplankton in the water column.",
                                                    "type": "Invertebrate",
                                                    "order": "Scleralcyonacea",
                                                    "family": "Pennatulidae",
                                                    "genus": "Ptilosarcus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/113028683/large.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Sea Walnut": {
                                                    "common": "Sea Walnut",
                                                    "scientific": "Mnemiopsis leidyi",
                                                    "info": "Initially native to the western Atlantic, the sea walnut has become invasive in Europe and Asia through ballast water discharges. A carnivore, they move slowly and with the flow of the current, feeding on various zooplankton.",
                                                    "type": "Invertebrate",
                                                    "order": "Lobata",
                                                    "family": "Bolinopsidae",
                                                    "genus": "Mnemiopsis",
                                                    "image_url": "https://i.imgur.com/Sxo66Hl.jpeg",
                                                    "breeding": "Below Average",
                                                    "region": "North America, South America",
                                                    "holdings": {},
                                                },
                                                "American Barn Owl": {
                                                    "common": "American Barn Owl",
                                                    "scientific": "Tyto furcata",
                                                    "info": "Once thought to be part of a cosmopolitan species, the populations of barn owls in the Americas are actually a different species entirely. The American barn owl is a specialized small mammal hunter, and like all barn owls, has a loud, shrieking call which can be disconcerting.",
                                                    "type": "Bird",
                                                    "order": "Strigiformes",
                                                    "family": "Tytonidae",
                                                    "genus": "Tyto",
                                                    "image_url": "https://ecoregistros.org/site/images/dataimages/2022/04/09/489008/DSC_0926.jpg",
                                                    "breeding": "Average",
                                                    "region": "North America, South America",
                                                    "holdings": {},
                                                },
                                                "Cuvier's Dwarf Caiman": {
                                                    "common": "Cuvier's Dwarf Caiman",
                                                    "scientific": "Paleosuchus palpebrosus",
                                                    "info": "The second-smallest of all crocodilians, the Cuvier's dwarf caiman only grows to 4.6 ft in length, but compensates for its lack of size with its heavy body armor and aggressive temperament. They feed on small animals such as fish and frogs, and are sometimes kept in the private trade but require large enclosures.",
                                                    "type": "Reptile",
                                                    "order": "Crocodilia",
                                                    "family": "Alligatoridae",
                                                    "genus": "Paleosuchus",
                                                    "image_url": "https://cdn.britannica.com/20/256820-050-C718F747/Cuviers-dwarf-caiman-Paleosuchus-palpebrosus.jpg",
                                                    "breeding": "Average",
                                                    "region": "South America",
                                                    "holdings": {
                                                        "North America": "1.1 - Essex County Zoo", },
                                                },
                                                "Pacific Giant Centipede": {
                                                    "common": "Pacific Giant Centipede",
                                                    "scientific": "Scolopendra subspinipes",
                                                    "info": "One of the most widespread and common members of its genus, the Pacific giant centipede is thought to have originated in Asia and Oceania, but has been introduced to many locations globally. They are are an aggressive and nervous species, highly defensive and considered an advanced centipede species for private keepers.",
                                                    "type": "Invertebrate",
                                                    "order": "Scolopendromorpha",
                                                    "family": "Scolopendridae",
                                                    "genus": "Scolopendra",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/254287959/large.jpg",
                                                    "breeding": "Below Average",
                                                    "region": "Asia, Oceania",
                                                    "holdings": {},
                                                },
                                                "Southern Flying Squirrel": {
                                                    "common": "Southern Flying Squirrel",
                                                    "scientific": "Glaucomys volans",
                                                    "info": "Out of the three North American flying squirrel species, the southern flying squirrel is distributed throughout the eastern part of the continent as well as into Mexico and Central America. A nocturnal species, they are highly social and fly together in large groups.",
                                                    "type": "Mammal",
                                                    "order": "Rodentia",
                                                    "family": "Sciuridae",
                                                    "genus": "Glaucomys",
                                                    "image_url": "https://i.imgur.com/hkY7q0D.jpeg",
                                                    "breeding": "Difficult",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "European Bombardier Beetle": {
                                                    "common": "European Bombardier Beetle",
                                                    "scientific": "Brachinus crepitans",
                                                    "info": "A common bombardier beetle species found throughout Europe, western Asia, and northern Africa. The larvae are parasites of beetle pupae, with the adults able to shoot their trademark noxious toxin at threats. They are usually found hiding under stones.",
                                                    "type": "Invertebrate",
                                                    "order": "Coleoptera",
                                                    "family": "Carabidae",
                                                    "genus": "Brachinus",
                                                    "image_url": "https://static.inaturalist.org/photos/114151390/large.jpg",
                                                    "breeding": "Average",
                                                    "region": "Europe, Asia, Africa",
                                                    "holdings": {},
                                                },
                                                "Western Pond Turtle": {
                                                    "common": "Western Pond Turtle",
                                                    "scientific": "Actinemys marmorata",
                                                    "info": "This turtle species is endemic to the western coast of North America, from Washington State to Baja California. Formerly native to western Canada, it was extirpated from that region by 2002. They are vulnerable to habitat loss and are listed as Vulnerable on the IUCN Red List.",
                                                    "type": "Reptile",
                                                    "order": "Testudines",
                                                    "family": "Emydidae",
                                                    "genus": "Actinemys",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/5198277/large.jpeg",
                                                    "breeding": "Below Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Rock Hyrax": {
                                                    "common": "Rock Hyrax",
                                                    "scientific": "Procavia capensis",
                                                    "info": "A member of the afrothere clade and therefore closely related to elephants, the rock hyrax is the most common and well-known of the hyrax species. Distributed across Africa, it is adaptable and lives in human-inhabited areas. They are highly social, living in groups of 10-80 animals.",
                                                    "type": "Mammal",
                                                    "order": "Hyracoidea",
                                                    "family": "Procaviidae",
                                                    "genus": "Procavia",
                                                    "image_url": "https://static.inaturalist.org/photos/131657139/large.jpeg",
                                                    "breeding": "Average",
                                                    "region": "Africa",
                                                    "holdings": {},
                                                },
                                                "Reticulated Glass Frog": {
                                                    "common": "Reticulated Glass Frog",
                                                    "scientific": "Hyalinobatrachium valerioi",
                                                    "info": "One of the more common glass frog species found in captivity, the reticulated glass frog is native to southern Central America to the Pacific slopes of South America in Colombia and Ecuador. They are carnivores, feeding on insects such as crickets, moths, and flies.",
                                                    "type": "Amphibian",
                                                    "order": "Anura",
                                                    "family": "Centrolenidae",
                                                    "genus": "Hyalinobatrachium",
                                                    "image_url": "https://static.inaturalist.org/photos/374495/large.jpg",
                                                    "breeding": "Below Average",
                                                    "region": "North America, South America",
                                                    "holdings": {},
                                                },
                                                "Green Frog": {
                                                    "common": "Green Frog",
                                                    "scientific": "Lithobates clamitans",
                                                    "info": "The green frog is one of the most common frogs found in North America. Distributed to the eastern part of the continent from Quebec to Texas, it has also been introduced to parts of Newfoundland. They are voracious predators and have strong territoriality, with males defending their territories fiercely.",
                                                    "type": "Amphibian",
                                                    "order": "Anura",
                                                    "family": "Ranidae",
                                                    "genus": "Lithobates",
                                                    "image_url": "https://static.inaturalist.org/photos/7660373/large.jpg",
                                                    "breeding": "Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "African Clawed Frog": {
                                                    "common": "African Clawed Frog",
                                                    "scientific": "Xenopus laevis",
                                                    "info": "One of the most well-studied amphibians due to its use as a model organism, the African clawed frog is native to freshwater environments of sub-Saharan Africa. They are very adaptable and found in high numbers in artifically created water bodies.",
                                                    "type": "Amphibian",
                                                    "order": "Anura",
                                                    "family": "Pipidae",
                                                    "genus": "Xenopus",
                                                    "image_url": "https://i.imgur.com/FPErRK0.jpeg",
                                                    "breeding": "Average",
                                                    "region": "Africa",
                                                    "holdings": {},
                                                },
                                                "Australian Green Tree Frog": {
                                                    "common": "Australian Green Tree Frog",
                                                    "scientific": "Ranoidea caerulea",
                                                    "info": "An arboreal frog native to Australia and New Guinea, the Australian green tree frog is one of the most common frogs in Australia and is a popular exotic pet as well. They are larger than most Australian frogs, growing to 4 inches in length, and can live for over 20 years.",
                                                    "type": "Amphibian",
                                                    "order": "Anura",
                                                    "family": "Hylidae",
                                                    "genus": "Ranoidea",
                                                    "image_url": "https://static.inaturalist.org/photos/388487926/large.jpg",
                                                    "breeding": "Average",
                                                    "region": "Oceania",
                                                    "holdings": {},
                                                },
                                                "Bear Lake Sculpin": {
                                                    "common": "Bear Lake Sculpin",
                                                    "scientific": "Cottus extensus",
                                                    "info": "A small sculpin growing no larger than 5 inches, the Bear Lake sculpin is, as the name suggests, endemic to Bear Lake on the border of Utah and Idaho. It has also been introduced to the nearby Flaming Gorge Reservoir and is considered Vulnerable on the IUCN Red List due to habitat loss.",
                                                    "type": "Fish",
                                                    "order": "Perciformes",
                                                    "family": "Cottidae",
                                                    "genus": "Cottus",
                                                    "image_url": "https://static.inaturalist.org/photos/248862981/original.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Mottled Sculpin": {
                                                    "common": "Mottled Sculpin",
                                                    "scientific": "Cottus bairdii",
                                                    "info": "A widely distributed sculpin found exclusively in North America, mottled sculpins prefer well-oxygenated coldwater habitats such as mountain streams and rocky lake shores. They feed mostly on aquatic insect larvae and grow to 5.9 inches in length.",
                                                    "type": "Fish",
                                                    "order": "Perciformes",
                                                    "family": "Cottidae",
                                                    "genus": "Cottus",
                                                    "image_url": "https://i.troutnut.com/im_regspec/pic_3150_800.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Paiute Sculpin": {
                                                    "common": "Paiute Sculpin",
                                                    "scientific": "Cottus beldingii",
                                                    "info": "Endemic to the western United States, the Paiute sculpin is a small fish, growing to 5 inches at maximum. They are benthic fish which almost never leave the substrate of their habitat, which is typically riffles in streams and creeks.",
                                                    "type": "Fish",
                                                    "order": "Perciformes",
                                                    "family": "Cottidae",
                                                    "genus": "Cottus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/18337097/original.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Egyptian Fruit Bat": {
                                                    "common": "Egyptian Fruit Bat",
                                                    "scientific": "Rousettus aegyptiacus",
                                                    "info": "A medium-sized bat native to Africa and Asia, the Egyptian fruit bat feeds on, as its name suggests, fruit and leaves. They are considered pests in their native range for their eating of crops, and this serves as a primary threat to the species in the wild.",
                                                    "type": "Mammal",
                                                    "order": "Chiroptera",
                                                    "family": "Pteropodidae",
                                                    "genus": "Rousettus",
                                                    "image_url": "https://live.staticflickr.com/3473/3186245424_ebb367a628_b.jpg",
                                                    "breeding": "Below Average",
                                                    "region": "Asia, Africa",
                                                    "holdings": {},
                                                },
                                                "Rodrigues Flying Fox": {
                                                    "common": "Rodrigues Flying Fox",
                                                    "scientific": "Pteropus rodricensis",
                                                    "info": "An endangered species of flying fox endemic to the Mauritian island of Rodridues, this large bat roosts in large groups during the day and forages for fruit at night. They are hunted extensively by locals as well as being threatened by storms that buffet the island.",
                                                    "type": "Mammal",
                                                    "order": "Chiroptera",
                                                    "family": "Pteropodidae",
                                                    "genus": "Pteropus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/459216418/large.jpeg",
                                                    "breeding": "Below Average",
                                                    "region": "Africa",
                                                    "holdings": {},
                                                },
                                                "California Skeleton Shrimp": {
                                                    "common": "California Skeleton Shrimp",
                                                    "scientific": "Caprella californica",
                                                    "info": "Despite the name, the California skeleton shrimp is an amphipod and not a true shrimp. They are native to marine habitats of the eastern Pacific, though there are reports of distribution in the Sea of Japan as well.",
                                                    "type": "Invertebrate",
                                                    "order": "Amphipoda",
                                                    "family": "Caprellidae",
                                                    "genus": "Caprella",
                                                    "image_url": "https://www.joelsartore.com/wp-content/uploads/stock/INV006/INV006-00336-1920x1279.jpg",
                                                    "breeding": "Difficult",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Common Basket Star": {
                                                    "common": "Common Basket Star",
                                                    "scientific": "Gorgonocephalus eucnemis",
                                                    "info": "Found in frigid waters of the Northern Hemisphere, the common basket star can be found from 8 to 1850 meters in the ocean. Their many arms allow them to grab particles of food from the water column. They are often found in association with toxic sponges, which allows it to defend itself from predators.",
                                                    "type": "Invertebrate",
                                                    "order": "Phrynophiurida",
                                                    "family": "Gorgonocephalidae",
                                                    "genus": "Gorgonocephalus",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/166133850/original.jpeg",
                                                    "breeding": "Impossible",
                                                    "region": "North America, Asia",
                                                    "holdings": {},
                                                },
                                                "Everglades Crayfish": {
                                                    "common": "Everglades Crayfish",
                                                    "scientific": "Procambarus alleni",
                                                    "info": "Endemic to Florida, the Everglades crayfish has many color variants from brown to blue, but its most well known variant is an electric blue coloration that was bred into it for the aquarium industry. They are an omnivorous scavenger and will eat just about anything.",
                                                    "type": "Invertebrate",
                                                    "order": "Decapoda",
                                                    "family": "Cambaridae",
                                                    "genus": "Procambarus",
                                                    "image_url": "https://static.inaturalist.org/photos/67827087/original.jpeg",
                                                    "breeding": "Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Sarcastic Fringehead": {
                                                    "common": "Sarcastic Fringehead",
                                                    "scientific": "Neoclinus blanchardi",
                                                    "info": "One of the largest blenny species, the sarcastic fringehead is found in the eastern Pacific and is most well known for its incredibly large jaws that they use to intimidate others and defend their territory. They reside within holes in the rock and come out typically only to eat.",
                                                    "type": "Fish",
                                                    "order": "Blenniiformes",
                                                    "family": "Chaenopsidae",
                                                    "genus": "Neoclinus",
                                                    "image_url": "https://static.inaturalist.org/photos/72790372/large.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Coyote": {
                                                    "common": "Coyote",
                                                    "scientific": "Canis latrans",
                                                    "info": "A canine native to North America, the coyote is an important mesopredator in the continent's ecosystem and is one of the most common large mammals there. Typically feeding on small mammals, coyotes are much less social than their cousins the gray wolf, preferring instead to live alone or in loosely-associated small packs.",
                                                    "type": "Mammal",
                                                    "order": "Carnivora",
                                                    "family": "Canidae",
                                                    "genus": "Canis",
                                                    "image_url": "https://i.imgur.com/2rGpbFD.jpeg",
                                                    "breeding": "Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Giant Desert Centipede": {
                                                    "common": "Giant Desert Centipede",
                                                    "scientific": "Scolopendra heros",
                                                    "info": "One of the most common centipedes kept in the private trade, the giant desert centipede is renowned for its bright color, but it is an aggressive species that should be kept with great care. Native to arid regions of North America, they hunt small animals at night using their venom and strength.",
                                                    "type": "Invertebrate",
                                                    "order": "Scolopendromorpha",
                                                    "family": "Scolopendridae",
                                                    "genus": "Scolopendra",
                                                    "images": [
                                                        {"label": "Black-headed form", "url": "https://i.imgur.com/EdI01Xr.jpeg"},
                                                    ],
                                                    "breeding": "Below Average",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Timber Rattlesnake": {
                                                    "common": "Timber Rattlesnake",
                                                    "scientific": "Crotalus horridus",
                                                    "info": "A large rattlesnake species native to the eastern half of the United States the timber rattlesnake is the only venomous snake species found in the northeastern part of the country. They are threatened in several states due to habitat loss.",
                                                    "type": "Reptile",
                                                    "order": "Squamata",
                                                    "family": "Viperidae",
                                                    "genus": "Crotalus",
                                                    "image_url": "https://i.imgur.com/3X7bUvC.jpeg",
                                                    "breeding": "Below Average",
                                                    "region": "North America",
                                                    "holdings": {
                                                        "North America": "1.1 - Tri-State Zoo & Aquarium", },
                                                },

                                                    "Common Pipistrelle": {
                                                    "common": "Common Pipistrelle",
                                                    "scientific": "Pipistrellus pipistrellus",
                                                    "info": "This small insectivorous bat can be found in a huge range from the British Isles to Korea. Preferring mosquitoes, midges, and gnats, they are an edge specialist, which means that they feed primarily on the edges of woodlands.",
                                                    "type": "Mammal",
                                                    "order": "Chiroptera",
                                                    "family": "Vespertilionidae",
                                                    "genus": "Pipistrellus",
                                                    "image_url": "https://batslife.eu/wp-content/uploads/2019/11/Vilda_36091_Rollin_Verlinde__Common_Pipistrelle-1160x741.jpg",
                                                    "breeding": "Difficult",
                                                    "region": "Europe, Asia",
                                                    "holdings": {},
                                                },
                                                "Raft Spider": {
                                                    "common": "Raft Spider",
                                                    "scientific": "Dolomedes fimbriatus",
                                                    "info": "A large semi-aquatic spider with a disjunct distribution from Iceland to Siberia, the raft spider primarily hunts aquatic invertebrates such as pond striders. They have been known to fully submerge themselves to protect themselves from predators, hiding underwater for several minutes.",
                                                    "type": "Invertebrate",
                                                    "order": "Araneae",
                                                    "family": "Dolomedidae",
                                                    "genus": "Dolomedes",
                                                    "image_url": "https://i.imgur.com/iVmHrhH.jpeg",
                                                    "breeding": "Below Average",
                                                    "region": "Europe, Asia",
                                                    "holdings": {},
                                                },
                                                "Wasp Spider": {
                                                    "common": "Wasp Spider",
                                                    "scientific": "Argiope bruennichi",
                                                    "info": "One of several orb weavers native to Eurasia, the wasp spider gets its name from the distinctive coloration on its abdomen and legs. Males are significantly smaller than females, and it is thought this allows them to sneak onto the females' webs in order to mate.",
                                                    "type": "Invertebrate",
                                                    "order": "Araneae",
                                                    "family": "Araneidae",
                                                    "genus": "Argiope",
                                                    "image_url": "https://live.staticflickr.com/65535/51891449875_2ca7876e01_b.jpg",
                                                    "breeding": "Average",
                                                    "region": "Europe, Asia, Africa",
                                                    "holdings": {},
                                                },
                                                "Indian White-Eye": {
                                                    "common": "Indian White-Eye",
                                                    "scientific": "Zosterops palpebrosus",
                                                    "info": "One of the many birds in the white-eye family, the Indian white-eye is a common passerine bird within the Indian subcontinent, feeding primarily on nectar and insects. They live in tightly-associated flocks which only separate during the breeding season.",
                                                    "type": "Bird",
                                                    "order": "Passeriformes",
                                                    "family": "Zosteropidae",
                                                    "genus": "Zosterops",
                                                    "image_url": "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/126366191/900",
                                                    "breeding": "Average",
                                                    "region": "Asia",
                                                    "holdings": {},
                                                },
                                                "New Caledonian Giant Gecko": {
                                                    "common": "New Caledonian Giant Gecko",
                                                    "scientific": "Rhacodactylus leachianus",
                                                    "info": "The largest extant species of gecko, the New Caledonian giant gecko is commonly known as the 'leachie' in the reptile hobby. They are primarily nocturnal, hiding within tree hollows during the daytime. They are omnivorous and not picky, feeding on fruit, nectar, sap, and small animals.",
                                                    "type": "Reptile",
                                                    "order": "Squamata",
                                                    "family": "Diplodactylidae",
                                                    "genus": "Rhacodactylus",
                                                    "image_url": "https://a-z-animals.com/media/2024/03/shutterstock-2338755981-huge-licensed-scaled-1024x682.jpg",
                                                    "breeding": "Difficult",
                                                    "region": "Oceania",
                                                    "holdings": {},
                                                },
                                                "Common Earwig": {
                                                    "common": "Common Earwig",
                                                    "scientific": "Forficula auricularia",
                                                    "info": "Also known as the European earwig, the common earwig gets its name from the hindwings, which look like a human ear when unfolded. They are a common household insect and are disliked due to their appearance, but are completely harmless.",
                                                    "type": "Invertebrate",
                                                    "order": "Dermaptera",
                                                    "family": "Forficulidae",
                                                    "genus": "Forficula",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/15939/large.jpg",
                                                    "breeding": "Average",
                                                    "region": "Europe, Asia",
                                                    "holdings": {},
                                                },
                                                "American Paddlefish": {
                                                    "common": "American Paddlefish",
                                                    "scientific": "Polyodon spathula",
                                                    "info": "One of the most iconic and well-known North American freshwater fish, the American paddlefish is the only remaining extant paddlefish species. They are filter feeders, keeping their mouths open most of the time to feed on zooplankton.",
                                                    "type": "Fish",
                                                    "order": "Acipenseriformes",
                                                    "family": "Polyodontidae",
                                                    "genus": "Polyodon",
                                                    "image_url": "https://i.imgur.com/Xibb9j5.jpeg",
                                                    "breeding": "Difficult",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "Blue-Green Chromis": {
                                                    "common": "Blue-Green Chromis",
                                                    "scientific": "Chromis viridis",
                                                    "info": "A common shoaling damselfish of the Indo-Pacific, the blue-green chromis is commonly kept in the aquarium trade, where it is renowned for its hardiness. They are found at shallow depths and are often associated with Acropora corals.",
                                                    "type": "Fish",
                                                    "order": "Blenniiformes",
                                                    "family": "Pomacentridae",
                                                    "genus": "Chromis",
                                                    "image_url": "https://www.fishi-pedia.com/wp-content/uploads/2023/05/Chromis_viridis_1-scaled.jpg",
                                                    "breeding": "Impossible",
                                                    "region": "Asia, Africa, Oceania",
                                                    "holdings": {},
                                                },
                                                "Percula Clownfish": {
                                                    "common": "Percula Clownfish",
                                                    "scientific": "Amphiprion percula",
                                                    "info": "One of the most popular and well-known clownfish species, the percula clownfish is endemic to the southeastern Indo-Pacific, from New Guinea to Australia. Associated with two species of sea anemones, the percula clownfish can be distinguished from the ocellaris clownfish by the number of dorsal spines.",
                                                    "type": "Fish",
                                                    "order": "Blenniiformes",
                                                    "family": "Pomacentridae",
                                                    "genus": "Amphiprion",
                                                    "image_url": "https://i.imgur.com/2wsxabc.jpeg",
                                                    "breeding": "Easy",
                                                    "region": "Oceania",
                                                    "holdings": {},
                                                },
                                                "Banggai Cardinalfish": {
                                                    "common": "Banggai Cardinalfish",
                                                    "scientific": "Pterapogon kauderni",
                                                    "info": "One of the few marine fish to have been bred in captivity, the Banggai cardinalfish is originally endemic to the Banggai archipelago of Indonesia. It is a paternal mouthbrooder, which makes it easier to breed in captivity than other cardinalfish. It is endangered in the wild and most aquarium specimens are captive-bred.",
                                                    "type": "Fish",
                                                    "order": "Gobiiformes",
                                                    "family": "Apogonidae",
                                                    "genus": "Pterapogon",
                                                    "image_url": "https://i.imgur.com/Fa8uTts.jpeg",
                                                    "breeding": "Average",
                                                    "region": "Asia",
                                                    "holdings": {},
                                                },
                                                "Ball Python": {
                                                    "common": "Ball Python",
                                                    "scientific": "Python regius",
                                                    "info": "A small python endemic to west and central Africa, the ball python is extremely popular in the private trade for its small size and docile temperament. Feeding primarily on small mammals and birds, this species has many morphs bred in captivity, including the controversial spider ball python.",
                                                    "type": "Reptile",
                                                    "order": "Squamata",
                                                    "family": "Pythonidae",
                                                    "genus": "Python",
                                                    "image_url": "https://i.imgur.com/b2G7noH.jpeg",
                                                    "breeding": "Easy",
                                                    "region": "Africa",
                                                    "holdings": {},
                                                },
                                                "Argentine Horned Frog": {
                                                    "common": "Argentine Horned Frog",
                                                    "scientific": "Ceratophrys ornata",
                                                    "info": "Endemic to South America, the Argentine horned frog is a voracious ambush predator. Typically remaining inactive while it waits for food, this brightly colored frog has sharp teeth and can inflict surprisingly serious injuries if threatened.",
                                                    "type": "Amphibian",
                                                    "order": "Anura",
                                                    "family": "Ceratophryidae",
                                                    "genus": "Ceratophrys",
                                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/106327834/original.jpeg",
                                                    "breeding": "Average",
                                                    "region": "South America",
                                                    "holdings": {},
                                                },
                                                "Carolina Sphinx Moth": {
                                                    "common": "Carolina Sphinx Moth",
                                                    "scientific": "Manduca sexta",
                                                    "info": "The larvae of this moth species are more well known than the adults. The larvae are known as tobacco hornworms, and they feed on various plants, with their preferred hosts being tobacco and tomato plants. They are commonly used as model organisms in biology due to their short life cycles.",
                                                    "type": "Invertebrate",
                                                    "order": "Lepidoptera",
                                                    "family": "Sphingidae",
                                                    "genus": "Manduca",
                                                    "images": [
                                                        {"label": "Tobacco hornworm", "url": "https://i.imgur.com/oHTzQ1u.jpeg"},
                                                        {"label": "Carolina sphinx moth", "url": "https://www.butterfliesandmoths.org/sites/default/files/bamona_images/manduca_sexta_taj1.jpg"}
                                                    ],
                                                    "breeding": "Very Easy",
                                                    "region": "North America",
                                                    "holdings": {},
                                                },
                                                "American Tadpole Shrimp": {
                                                    "common": "American Tadpole Shrimp",
                                                    "scientific": "Triops longicaudatus",
                                                    "info": "The American tadpole shrimp is a freshwater crustacean species that is the most widespread of all tadpole shrimp. Despite the name implying endemism to North America, it can be found there, South America, Japan, South Korea, and isladns in the Indo-Pacific.",
                                                    "type": "Invertebrate",
                                                    "order": "Notostraca",
                                                    "family": "Triopsidae",
                                                    "genus": "Triops",
                                                    "image_url": "https://i.imgur.com/7GJGKH8.jpeg",
                                                    "breeding": "Easy",
                                                    "region": "North America, South America, Asia, Oceania",
                                                    "holdings": {},
                                                },
                                                "San Francisco Brine Shrimp": {
                                                    "common": "San Francisco Brine Shrimp",
                                                    "scientific": "Artemia franciscana",
                                                    "info": "The brine shrimp most familiar to aquarists, the San Francisco brine shrimp is originally native to the Americas, but has now become a global species due to introductions. It is often raised for live food or for petkeeping in the aquarium trade.",
                                                    "type": "Invertebrate",
                                                    "order": "Anostraca",
                                                    "family": "Artemiidae",
                                                    "genus": "Artemia",
                                                    "image_url": "https://microscopy.org/get/files/image/galleries/Artemia_FINAL.jpg",
                                                    "breeding": "Very Easy",
                                                    "region": "North America, South America",
                                                    "holdings": {},
                                                },
                                                "African Fat-Tailed Gecko": {
                                                    "common": "African Fat-Tailed Gecko",
                                                    "scientific": "Hemitheconyx caudicinctus",
                                                    "info": "A ground-dwelling gecko originally native to west and central Africa, the African fat-tailed gecko gets its name from its large tail, which is predictably used to store fat for lean times. They are popular pets and many morphs have been bred into them for this purpose.",
                                                    "type": "Reptile",
                                                    "order": "Squamata",
                                                    "family": "Eublepharidae",
                                                    "genus": "Hemitheconyx",
                                                    "image_url": "https://i.imgur.com/JwCxcU1.jpeg",
                                                    "breeding": "Average",
                                                    "region": "Africa",
                                                    "holdings": {},
                                                    },
                                                "Cryptic Green Pit Viper": {
                                                    "common": "Cryptic Green Pit Viper",
                                                    "scientific": "Trimeresurus cryptographicus",
                                                    "info": "One of the most recently described members in its genus, the cryptic green pit viper was only made known to science in 2025. At the moment it is only known from a small area in central Thailand, and has a variety of color variants, making it difficult to identify to species level.",
                                                    "type": "Reptile",
                                                    "order": "Squamata",
                                                    "family": "Viperidae",
                                                    "genus": "Trimeresurus",
                                                    "image_url": "https://static.thainationalparks.com/img/species/2025/04/26/398196/trimeresurus-cryptographicus-w-1500.jpg",
                                                    "breeding": "Below Average",
                                                    "region": "Asia",
                                                    "holdings": {},
                                                },
                                                "Guo's Green Pit Viper": {
                                                "common": "Guo's Green Pit Viper",
                                                "scientific": "Trimeresurus guoi",
                                                "info": "A recently described species of pit viper, the Guo's green pit viper is found in China, Thailand, Laos, and Myanmar. It is closely related to the white-lipped pit viper and was thought to be the same species until it was described as a separate species in 2021.",
                                                "type": "Reptile",
                                                "order": "Squamata",
                                                "family": "Viperidae",
                                                "genus": "Trimeresurus",
                                                "image_url": "https://static.thainationalparks.com/img/species/2020/12/03/397103/trimeresurus-albolabris-guoi-w-1500.jpg",
                                                "breeding": "Below Average",
                                                "region": "Asia",
                                                "holdings": {},
                                            },
                                            "Lanna Green Pit Viper": {
                                            "common": "Lanna Green Pit Viper",
                                            "scientific": "Trimeresurus lanna",
                                            "info": "Named after the ancient Lan kingdom, the Lanna green pit viper is a recently described pit viper endemic to a small area of Thailand. Some specimens have impressive accents on their scales, which can help in identification.",
                                            "type": "Reptile",
                                            "order": "Squamata",
                                            "family": "Viperidae",
                                            "genus": "Trimeresurus",
                                            "image_url": "https://static.thainationalparks.com/img/species/2024/04/05/398115/trimeresurus-lanna-w-1500.jpg",
                                            "breeding": "Below Average",
                                            "region": "Asia",
                                            "holdings": {},
                                        },
                                        "Phuket Pit Viper": {
                                        "common": "Phuket Pit Viper",
                                        "scientific": "Trimeresurus phuketensis",
                                        "info": "Endemic to Phuket Island, the Phuket pit viper is distinguished from other species in its genus with its impressive red and green coloration, which varies in brightness per individual. They are a fully arboreal species that feeds on small tree-dwelling animals.",
                                        "type": "Reptile",
                                        "order": "Squamata",
                                        "family": "Viperidae",
                                        "genus": "Trimeresurus",
                                        "image_url": "https://i.imgur.com/OiAHhh8.jpeg",
                                        "breeding": "Below Average",
                                        "region": "Asia",
                                        "holdings": {},
                                    },
                                    "Omkoi Lance-Headed Pit Viper": {
                                    "common": "Omkoi Lance-Headed Pit Viper",
                                    "scientific": "Protobothrops kelomohy",
                                    "info": "One of the rarest vipers in the world, the Omkoi lance-headed pit viper is endemic to the Omkoi district of northern Thailand. They are well-known for their elusiveness, only being seen in situ once after being described in 2020.",
                                    "type": "Reptile",
                                    "order": "Squamata",
                                    "family": "Viperidae",
                                    "genus": "Protobothrops",
                                    "image_url": "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjkNUZ_xwrA_D0SGEBtDMeUb3VtE7h9bYFqx43Y0AteAn5re4icyJ9cWlQjnhyphenhyphenaMj2XNdXVsawrwNd7j_1vsnJXfuwO2JSmsy5L8K-3GSI5yHEI2-AUk1TtyCa0WJYHIALBqd82vkd-X1nS/s1600/Protobothrops_kelomohy-novataxa_2020-Sumontha_Vasaruchapong_Chomngam_Suntrarachun_et-al.jpg",
                                    "breeding": "Difficult",
                                    "region": "Asia",
                                    "holdings": {},
                                    },
                                    "Brown-Banded Cobra": {
                                    "common": "Brown-Banded Cobra",
                                    "scientific": "Naja fuxi",
                                    "info": "Once thought to be subsumed under the monocled cobra, the brown-banded cobra is a genetically distinct yet morphologically identical species to the more common parent species. As juveniles they are distinct in coloration, which allows individuals to be determined to species level.",
                                    "type": "Reptile",
                                    "order": "Squamata",
                                    "family": "Elapidae",
                                    "genus": "Naja",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/157430744/large.jpg",
                                    "breeding": "Below Average",
                                    "region": "Asia",
                                    "holdings": {},
                                    },
                                    "Siamese Spitting Cobra": {
                                    "common": "Siamese Spitting Cobra",
                                    "scientific": "Naja siamensis",
                                    "info": "Found in mainland Southeast Asia, the Siamese spitting cobra is a highly venomous species regarded as potentially dangerous due to its temperament and commonality in human settlements. Feeding on rodents, the famous spitting behavior is a defensive and not an offensive one.",
                                    "type": "Reptile",
                                    "order": "Squamata",
                                    "family": "Elapidae",
                                    "genus": "Naja",
                                    "image_url": "https://i.imgur.com/OC275xR.jpeg",
                                    "breeding": "Below Average",
                                    "region": "Asia",
                                    "holdings": {},
                                        },
                                        "Wanghaoting's Krait": {
                                        "common": "Wanghaoting's Krait",
                                        "scientific": "Bungarus wanghaotingi",
                                        "info": "A highly venomous krait species that is native to mainland Southeast Asia from China to Myanmar, the Wanghaoting's krait is named for Mr. Wang Hao-t'ing, an artist and scientist who painted reptiles for western scientists. They are often synonymized with the similar many-banded krait.",
                                        "type": "Reptile",
                                        "order": "Squamata",
                                        "family": "Elapidae",
                                        "genus": "Bungarus",
                                        "image_url": "https://images.squarespace-cdn.com/content/v1/5b4b10e19d5abb222d2069fe/1617092002655-MDPCXWMDX073CX6L3Y23/Many+Banded+Krait+-+Bungarus+multicinctus.jpg",
                                        "breeding": "Below Average",
                                        "region": "Asia",
                                        "holdings": {},
                                    },
                                    "Cox's Mud Snake": {
                                    "common": "Cox's Mud Snake",
                                    "scientific": "Homalopsis mereljcoxi",
                                    "info": "Also known as the skull-faced water snake, this species is distinguished by its distinctive patterning on the top of the head. A nocturnal species, they come out at night to hunt prey such as fish and frogs. They are viviparous, giving birth to live young.",
                                    "type": "Reptile",
                                    "order": "Squamata",
                                    "family": "Homalopsidae",
                                    "genus": "Homalopsis",
                                    "image_url": "https://www.thainationalparks.com/img/species/2020/09/03/397029/homalopsis-mereljcoxi-w-1500.jpg",
                                    "breeding": "Below Average",
                                    "region": "Asia",
                                    "holdings": {},
                                    },
                                    "Green Cat Snake": {
                                    "common": "Green Cat Snake",
                                    "scientific": "Boiga cyanea",
                                    "info": "A colubrid found in south, east, and southeast Asia, the green cat snake is a nonvenomous widely distributed species. They are both terrestrial and arboreal, feeding primarily on lizards but also taking frogs, birds, rodents, and other snakes.",
                                    "type": "Reptile",
                                    "order": "Squamata",
                                    "family": "Colubridae",
                                    "genus": "Boiga",
                                    "image_url": "https://static.thainationalparks.com/img/species/2017/08/10/319997/boiga-cyanea-w-1500.jpg",
                                    "breeding": "Average",
                                    "region": "Asia",
                                    "holdings": {},
                                    },
                                    "Eggers's Killifish": {
                                    "common": "Eggers's Killifish",
                                    "scientific": "Nothobranchius eggersi",
                                    "info": "Endemic to Tanzania, the Eggers's killifish has several distinct morphs of red and blue. It is a fairly recently described species, being described in 1982 from Utete in Tanzania. They reach about 2 inches at maximum size, a moderate size for a Nothobranchius.",
                                    "type": "Fish",
                                    "order": "Cyprinodontiformes",
                                    "family": "Nothobranchiidae",
                                    "genus": "Nothobranchius",
                                    "images": [
                                        {"label": "Utete Red locality", "url": "https://www.seriouslyfish.com/wp-content/uploads/2012/05/Nothobranchius-Eggersi-Utete.jpg"},
                                        {"label": "Makurunge locality", "url": "https://killis.org.uk/wp-content/uploads/2021/04/Nothobranchius-eggersi-Makurunge-TAN-19-19-%C2%A9-Kiril-kardashev-Text-x-2048x1247.jpg"}
                                    ],
                                    "breeding": "Average",
                                    "region": "Africa",
                                    "holdings": {},
                                    },
                                    "Featherfin Pearlfish": {
                                    "common": "Featherfin Pearlfish",
                                    "scientific": "Simpsonichthys constanciae",
                                    "info": "A highly endangered killfish species endemic to Brazil, the featherfin pearlfish gets its name from the long fin extensions on its dorsal and anal fins. It can only be found in the São João river basin near Rio de Jainero. It grows up to 2.4 inches in total length.",
                                    "type": "Fish",
                                    "order": "Cyprinodontiformes",
                                    "family": "Rivulidae",
                                    "genus": "Simpsonichthys",
                                    "image_url": "https://i.imgur.com/mol9w0S.jpeg",
                                    "breeding": "Average",
                                    "region": "South America",
                                    "holdings": {},
                                        },
                                "Leaf Plate Montipora": {
                                    "common": "Leaf Plate Montipora",
                                    "scientific": "Montipora capricornis",
                                    "info": "Also known as the vase coral, cap coral, or plating montipora, the leaf plate montipora is a common Indo-Pacific SPS coral. Forming flat, plating colonies, this species comes in several color variants, some of which are common in the aquarium industry. At night the polyps emerge from their coralites in the skeleton to feed on plankton.",
                                    "type": "Invertebrate",
                                    "order": "Scleractinia",
                                    "family": "Acroporidae",
                                    "genus": "Montipora",
                                        "images": [
                                            {"label": "Brown form", "url": "https://i.imgur.com/rUBNLQf.png"},
                                             {"label": "Pink form", "url": "https://www.coralsoftheworld.org/media/images/0268_C01_05.jpg"},
                                            {"label": "Orange form", "url": "https://i.imgur.com/zuy7FyD.jpeg"},
                                        ],
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/485277548/large.jpg",
                                        "breeding": "Difficult",
                                        "region": "Asia, Oceania",
                                        "holdings": {},
                                    },
                                    "Thin Staghorn Coral": {
                                    "common": "Thin Staghorn Coral",
                                    "scientific": "Acropora tenuis",
                                    "info": "A somewhat common Acropora both in the wild and in captivity, the thin staghorn coral occurs in corymbose (thick, chaotic) colonies which can be several different colors. They typically occur in upper and mid-level reefs from 26-66 feet in depth.",
                                    "type": "Invertebrate",
                                    "order": "Scleractinia",
                                    "family": "Acroporidae",
                                    "genus": "Acropora",
                                    "images": [
                                        {"label": "Brown form", "url": "https://i.imgur.com/rxbFlyU.jpeg"},
                                        {"label": "Pink form", "url": "https://www.coralsoftheworld.org/media/images/0074_C01_02.jpg"},
                                        {"label": "Green form", "url": "https://www.coralsoftheworld.org/media/images/0074_C04_05.jpg"},
                                ],
                                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/37452375/large.jpg",
                                "breeding": "Difficult",
                                "region": "Asia, Africa, Oceania",
                                "holdings": {},
                                    },
                                    "Bog Turtle": {
                                    "common": "Bog Turtle",
                                    "scientific": "Glyptemys muhlenbergii",
                                    "info": "One of the most endangered species of turtles in the world, the bog turtle can be found in eastern North America, in a disjunct range from New York to Georgia. With a low reproduction rate, they are regarded as difficult to breed and are held in several zoos as a captive safety net population.",
                                    "type": "Reptile",
                                    "order": "Testudines",
                                    "family": "Emydidae",
                                    "genus": "Glyptemys",
                                    "image_url": "https://dep.nj.gov/njfw/wp-content/uploads/njfw/bog_turtle_2_Zarate.jpg",
                                    "breeding": "Difficult",
                                    "region": "North America",
                                    "holdings": {},
                                    },
                                    "Gooty Sapphire Tarantula": {
                                    "common": "Gooty Sapphire Tarantula",
                                    "scientific": "Poecilotheria metallica",
                                    "info": "A rare, critically endangered tarantula endemic to the tropical forests of southern India. This species is extremely popular in the invertebrate hobby for its bright coloration, but it is an aggressive and fast species that must be handled with care.",
                                    "type": "Invertebrate",
                                    "order": "Araneae",
                                    "family": "Theraphosidae",
                                    "genus": "Poecilotheria",
                                    "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/65568901/large.jpg",
                                    "breeding": "Below Average",
                                    "region": "Asia",
                                        "holdings": {
                                            "North America": "2.1 - Essex County Zoo", },
                                    },
    "Ring-Tailed Lemur": {
        "common": "Ring-Tailed Lemur",
        "scientific": "Lemur catta",
        "info": "The most well-known and common lemur species in captivity, the ring-tailed lemur resides in the dry forests of southwestern Madagascar. With a complex matriarchal social structure, ring-tailed lemurs are probably the most well-studied lemur species due to their ease of access and popularity amongst the general public.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Lemuridae",
        "genus": "Lemur",
        "image_url": "https://i.imgur.com/kWwTlWA.jpeg",
        "breeding": "Easy",
        "region": "Africa",
        "holdings": {
            "North America": "2.2 - Las Vegas Zoo",
            "Europe": "2.2 - Brussels Zoo"
        }
},
                                    "Spotted Turtle": {
                                    "common": "Spotted Turtle",
                                    "scientific": "Clemmys guttata",
                                    "info": "The only member of its monotypic genus, the spotted turtle is an endangered turtle species found in eastern North America. It is a sensitive indicator species, often being extirpated from areas with heavy pollution, and can be used to determine pristine freshwater ecosystems.",
                                    "type": "Reptile",
                                    "order": "Testudines",
                                    "family": "Emydidae",
                                    "genus": "Clemmys",
                                    "image_url": "https://i.imgur.com/EoLI22p.jpeg",
                                    "breeding": "Below Average",
                                    "region": "North America",
                                    "holdings": {},
                                },
                                "American Burying Beetle": {
                                "common": "American Burying Beetle",
                                "scientific": "Nicrophorus americanus",
                                "info": "One of the few beetles to exhibit parental care of its offspring, the American burying beetle is a species of carrion beetle that is native to North America. They bury their larvae in the soil, where they will feed on a carcass provided by the parents until they are adults.",
                                "type": "Invertebrate",
                                "order": "Coleoptera",
                                "family": "Staphylinidae",
                                "genus": "Nicrophorus",
                                "image_url": "https://static.inaturalist.org/photos/57135673/large.jpg",
                                "breeding": "Below Average",
                                "region": "North America",
                                "holdings": {},
                                },
                                "Dama Gazelle": {
                                "common": "Dama Gazelle",
                                "scientific": "Nanger dama",
                                "info": "Divided into three subspecies, the dama gazelle is one of the most endangered large mammals on Earth. With less than 300 individuals in the wild, this species is part of a large captive breeding program dedicated towards preserving the species in captivity.",
                                "type": "Mammal",
                                "order": "Artiodactyla",
                                "family": "Bovidae",
                                "genus": "Nanger",
                                    "images": [
                                        {"label": "Addra gazelle (ruficollis)", "url": "https://i.imgur.com/s0nkxoD.jpeg"}
                                    ],
                                "breeding": "Below Average",
                                "region": "Africa",
                                "holdings": {},
                                },
                                "White-Lipped Deer": {
                                "common": "White-Lipped Deer",
                                "scientific": "Cervus albirostris",
                                "info": "A large deer species endemic to the Tibetan Plateau and central China, the white-lipped deer is a rarely-encountered species both in captivity and the wild. They typically live in herds of at least 10 individuals, consisting of single-sex groups except for the breeding season.",
                                "type": "Mammal",
                                "order": "Artiodactyla",
                                "family": "Cervidae",
                                "genus": "Cervus",
                                "image_url": "https://i.imgur.com/Au3bG5e.jpeg",
                                "breeding": "Average",
                                "region": "Asia",
                                "holdings": {},
                                },
                                "Addax": {
                                "common": "Addax",
                                "scientific": "Addax nasomaculatus",
                                "info": "Originally native to the Sahara Desert, overhunting has led the addax to become critically endangered in the wild. They are thankfully hardy and do well in captivity, so a large captive safety population has been formed. They are able to survive off very little water, getting most of their water from plants they eat.",
                                "type": "Mammal",
                                "order": "Artiodactyla",
                                "family": "Bovidae",
                                "genus": "Addax",
                                "image_url": "https://i.imgur.com/ENyGrqT.jpeg",
                                "breeding": "Average",
                                "region": "Africa",
                                "holdings": {},
                                },
                                "Scimitar-Horned Oryx": {
                                "common": "Scimitar-Horned Oryx",
                                "scientific": "Oryx dammah",
                                "info": "Also known as the scimitar oryx and the Sahara oryx, the scimitar-horned oryx was originally native to wide swathes of the northern half of Africa, though it was made extinct in the wild due to overhunting. Captive breeding programs keep the species alive, and reintroduction programs have met with success.",
                                "type": "Mammal",
                                "order": "Artiodactyla",
                                "family": "Bovidae",
                                "genus": "Oryx",
                                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/67404875/original.jpg",
                                "breeding": "Average",
                                "region": "Africa",
                                "holdings": {},
                                    },

                                    "Mountain Zebra": {
                                        "common": "Mountain Zebra",
                                        "scientific": "Equus zebra",
                                        "info": "One of three extant zebra species, the mountain zebra is endemic to southern Africa. It has a disjunct and small range, and is listed by the IUCN Red List as a vulnerable species. They can be distinguished from the other two zebra species with their distinctive dewlap.",
                                        "type": "Mammal",
                                        "order": "Perissodactyla",
                                        "family": "Equidae",
                                        "genus": "Equus",
                                        "images": [
                                            {"label": "Hartmann's mountain zebra (hartmannae)", "url": "https://i.imgur.com/uhFZjcR.jpeg"},
                                        ],
                                        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Equus_zebra_hartmannae_-_Etosha_2015.jpg/1280px-Equus_zebra_hartmannae_-_Etosha_2015.jpg",
                                        "breeding": "Average",
                                        "region": "Africa",
                                        "holdings": {},
                                        },
                                        "Sardinian Brook Salamander": {
                                        "common": "Sardinian Brook Salamander",
                                        "scientific": "Euproctus platycephalus",
                                        "info": "This endangered salmander is endemic to Sardinia, an island off the coast of Italy. It is endangered due to habitat fragmentation, pollution, and habitat loss on the island. Mostly aquatic, they are known to aestivate on land to escape high temperatures and dryness.",
                                        "type": "Amphibian",
                                        "order": "Urodela",
                                        "family": "Salamandridae",
                                        "genus": "Euproctus",
                                        "image_url": "https://www.pierrewildlife.com/wp-content/uploads/2024/07/Euproctus-platycephalus-2.jpg",
                                        "breeding": "Below Average",
                                        "region": "Europe",
                                        "holdings": {},
                                        },
                                          "Alpine Newt": {
                                            "common": "Alpine Newt",
                                            "scientific": "Mesotriton alpestris",
                                            "info": "A common and widespread species of newt endemic to Europe, the alpine newt is well known for the males' bright coloration and its presence in the private trade. Juveniles start life on land and eventually move into water where they live the rest of their lives.",
                                            "type": "Amphibian",
                                            "order": "Urodela",
                                            "family": "Salamandridae",
                                            "genus": "Mesotriton",
                                            "images": [
                                                {"label": "Southern Italian alpine newt (inexpectatus)", "url": "https://i.imgur.com/1GnuTHk.jpeg"},
                                            ],
                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/165780401/large.jpg",
                                            "breeding": "Average",
                                            "region": "Europe",
                                            "holdings": {},
                                            },
                                            "Dark-Eyed Junco": {
                                            "common": "Dark-Eyed Junco",
                                            "scientific": "Junco hyemalis",
                                            "info": "A common and well-known North American passerine, the dark-eyed junco has a wide range depending on the time of year. It has been recorded from far northern Alaska to northern Mexico, showing its adaptability. They feed mostly on insects, seeds, and berries.",
                                            "type": "Bird",
                                            "order": "Passeriformes",
                                            "family": "Passerellidae",
                                            "genus": "Junco",
                                            "image_url": "https://i.imgur.com/OhP7pzl.jpeg",
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                            },
                                            "Northwestern Garter Snake": {
                                            "common": "Northwestern Garter Snake",
                                            "scientific": "Thamnophis ordinoides",
                                            "info": "Endemic to the northwestern part of North America from British Columbia to California, the northwestern garter snake is a common colubrid within its range. Preying on slugs, salamanders, and frogs, they can mostly be found on the edges of meadows near forests, making them an edge specialist species.",
                                            "type": "Reptile",
                                            "order": "Squamata",
                                            "family": "Colubridae",
                                            "genus": "Thamnophis",
                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/33871044/original.jpeg",
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                            },
                                              "Domestic Goose": {
                                                "common": "Domestic Goose",
                                                "scientific": "Anser anser domesticus",
                                                "info": "Descending from the greylag goose, the domestic goose has been bred into a variety of different forms, either for food or personal petkeeping. They are culturally relevant animals especially in Europe, where they were first domesticated.",
                                                "type": "Bird",
                                                "order": "Anseriformes",
                                                "family": "Anatidae",
                                                "genus": "Anser",
                                                "images": [
                                                    {"label": "Roman", "url": "https://livestockconservancy.org/wp-content/uploads/2022/08/Roman-Geese.jpg"},
                                                ],
                                                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/338955166/large.jpg",
                                                "breeding": "Very Easy",
                                                },
                                                "Atlantic Needlefish": {
                                                "common": "Atlantic Needlefish",
                                                "scientific": "Strongylura marina",
                                                "info": "A common demersal needlefish found in the western Atlantic, this species is also able to inhabit freshwater and has been introduced to the Tennessee River. They have been known to jump out of the water and impale people out at sea.",
                                                "type": "Fish",
                                                "order": "Beloniformes",
                                                "family": "Belonidae",
                                                "genus": "Strongylura",
                                                "image_url": "https://i.imgur.com/4jDRfVT.jpeg",
                                                "breeding": "Impossible",
                                                "region": "North America, South America",
                                                "holdings": {},
                                                },
                                                "Eastern Oyster": {
                                                "common": "Eastern Oyster",
                                                "scientific": "Crassostrea virginica",
                                                "info": "One of the most well-known oyster species, the eastern oyster is native to the eastern coast of North and South America. They are heavily impacted by climate change; a population survey of the Chesapeake Bay estimates less than 1% of the population remains. They are considered Vulnerable on the IUCN Red List.",
                                                "type": "Invertebrate",
                                                "order": "Ostreida",
                                                "family": "Ostreidae",
                                                "genus": "Crassostrea",
                                                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/9292868/original.png",
                                                "breeding": "Difficult",
                                                "region": "North America, South America",
                                                "holdings": {},
                                                },
                                                "Northern Puffer": {
                                                "common": "Northern Puffer",
                                                "scientific": "Sphoeroides maculatus",
                                                "info": "Endemic to the Atlantic coast of North America, the northern puffer can be found in both brackish and saltwater environments. With their sharp beak, they are able to extract shellfish from their shells and crush them. Unlike many other pufferfish species, their flesh is not poisonous.",
                                                "type": "Fish",
                                                "order": "Tetraodontiformes",
                                                "family": "Tetraodontidae",
                                                "genus": "Sphoeroides",
                                                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/65346027/large.jpg",
                                                "breeding": "Impossible",
                                                "region": "North America",
                                                "holdings": {},
                                                },
                                                "Spot Croaker": {
                                                "common": "Spot Croaker",
                                                "scientific": "Leiostomus xanthurus",
                                                "info": "A small croaker species that can be found from the Gulf of Maine to the Caribbean, the spot croaker is a culturally and commercially important species within its range. Growing from 9-14 inches in max size, they are frequently caught by sport anglers and fishermen, and are considered desirable quarry.",
                                                "type": "Fish",
                                                "order": "Acanthuriformes",
                                                "family": "Sciaenidae",
                                                "genus": "Leiostomus",
                                                "image_url": "https://static.inaturalist.org/photos/163271805/large.jpg",
                                                "breeding": "Impossible",
                                                "region": "North America",
                                                "holdings": {},
                                                },
                                                "Bocourt's Mud Snake": {
                                                "common": "Bocourt's Mud Snake",
                                                "scientific": "Subsessor bocourti",
                                                "info": "A semi-aquatic snake species endemic to mainland Southeast Asia, the Bocourt's mud snake is sometimes persecuted for food and for its scales. Feeding on frogs, fish and crustaceans, they are common despite the aforementioned persecution, and are listed as Least Concern on the IUCN Red List.",
                                                "type": "Reptile",
                                                "order": "Squamata",
                                                "family": "Homalopsidae",
                                                "genus": "Subsessor",
                                                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/103705161/original.jpg",
                                                "breeding": "Below Average",
                                                "region": "Asia",
                                                "holdings": {},
                                                },
                                                "Checkered Keelback": {
                                                "common": "Checkered Keelback",
                                                "scientific": "Fowlea piscator",
                                                "info": "Despite sharing a name with the venomous keelbacks, the checkered keelback is a non-venomous and totally harmless species of snake native to a wide swathe of south, east, and southeast Asia. They feed primarily on fish, amphibians, and aquatic invertebrates, and are active at day and night.",
                                                "type": "Reptile",
                                                "order": "Squamata",
                                                "family": "Colubridae",
                                                "genus": "Fowlea",
                                                "image_url": "https://i.imgur.com/HIquxHo.jpeg",
                                                "breeding": "Below Average",
                                                "region": "Asia",
                                                "holdings": {},
                                                },
                                                "Rainbow Water Snake": {
                                                "common": "Rainbow Water Snake",
                                                "scientific": "Enhydris enhydris",
                                                "info": "A mildly venomous colubrid with a large range spanning tropical Asia, the rainbow water snake is mainly piscivorous, relying on water-based habitats for its survival. They are adaptable and can be found in human-disturbed areas such as rice paddies and garden ponds.",
                                                "type": "Reptile",
                                                "order": "Squamata",
                                                "family": "Homalopsidae",
                                                "genus": "Enhydris",
                                                "image_url": "https://i.imgur.com/lEFT18s.jpeg",
                                                "breeding": "Below Average",
                                                "region": "Asia",
                                                "holdings": {},
                                                },
                                                "Bonnethead Shark": {
                                                "common": "Bonnethead Shark",
                                                "scientific": "Sphyrna tiburo",
                                                "info": "The smallest hammerhead shark species, the bonnethead shark was originally thought to have a wide pan-American distribution, but a recent split isolated the original species to the Caribbean and the Mid-Atlantic. They are one of the few omnivorous sharks, consuming abundant seagrass in an apparent attempt to help in digestion of their food.",
                                                "type": "Fish",
                                                "order": "Carcharhiniformes",
                                                "family": "Sphyrnidae",
                                                "genus": "Sphyrna",
                                                "image_url": "https://i.imgur.com/qiKPzmO.jpeg",
                                                "breeding": "Difficult",
                                                "region": "North America",
                                                "holdings": {},
                                                },
                                                "Eastern Crayfish": {
                                                "common": "Eastern Crayfish",
                                                "scientific": "Cambarus bartonii",
                                                "info": "One of the most well-known and abundant crayfish within its range, the eastern crayfish can be found from eastern Canada all the way down south to Alabama and Georgia. Preferring fast-flowing, cool rocky streams and shallow lakes, they are considered Least Concern on the IUCN Red List.",
                                                "type": "Invertebrate",
                                                "order": "Decapoda",
                                                "family": "Cambaridae",
                                                "genus": "Cambarus",
                                                "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/180992668/original.jpg",
                                                "breeding": "Below Average",
                                                "region": "North America",
                                                "holdings": {},
                                                },
                                                "Horn Shark": {
                                                "common": "Horn Shark",
                                                "scientific": "Heterodontus francisci",
                                                "info": "A common bullhead shark endemic to the northeastern Pacific, the horn shark gets its name from the spine on its first dorsal fin. A hardy and adaptable species, it is commonly found in aquaria due to this, and it is an approachable species even in its natural habitat.",
                                                "type": "Fish",
                                                "order": "Heterodontiformes",
                                                "family": "Heterodontidae",
                                                "genus": "Heterodontus",
                                                "image_url": "https://www.sharksandrays.com/wp-content/uploads/2020/11/Horn-Shark-006.jpg",
                                                "breeding": "Below Average",
                                                "region": "North America",
                                                "holdings": {},
                                                },
                                                "Snakehead Betta": {
                                                "common": "Snakehead Betta",
                                                "scientific": "Betta channoides",
                                                "info": "Endemic to Borneo, the snakehead betta is one of the most common and popular wild bettas in the aquarium trade. Typically a cryptic brown, this species will color up to bright red, black, and white when displaying. They are endemic to blackwater streams with acidic pH.",
                                                "type": "Fish",
                                                "order": "Anabantiformes",
                                                "family": "Osphronemidae",
                                                "genus": "Betta",
                                                "image_url": "https://i.imgur.com/lhUXwvn.png",
                                                "breeding": "Average",
                                                "region": "Asia",
                                                "holdings": {},
                                                },
                                                "Painted Turtle": {
                                                "common": "Painted Turtle",
                                                "scientific": "Chrysemys picta",
                                                "info": "The most widespread native turtle in North America, the painted turtle can be found from western Canada all the way down to Mexico and Alabama. There are 3 distinct subspecies, which were formerly their own species. Their bright coloration allows them to be differentiated from other native turtles.",
                                                "type": "Reptile",
                                                "order": "Testudines",
                                                "family": "Emydidae",
                                                "genus": "Chrysemys",
                                                "image_url": "https://i.imgur.com/iRdHhEB.jpeg",
                                                "breeding": "Average",
                                                "region": "North America",
                                                "holdings": {},
                                            },
                                            "Blue Surgeonfish": {
                                            "common": "Blue Surgeonfish",
                                            "scientific": "Paracanthurus hepatus",
                                            "info": "One of the most iconic and well-known saltwater fish, the blue surgeonfish goes by a litany of other common names, including hippo tang, palette tang, and hepatus tang. They can be found in a huge swathe of ocean from the east coast of Africa to the central Pacific.",
                                            "type": "Fish",
                                            "order": "Acanthuriformes",
                                            "family": "Acanthuridae",
                                            "genus": "Paracanthurus",
                                            "image_url": "https://i.imgur.com/AlpW9hj.jpeg",
                                            "breeding": "Average",
                                            "region": "Asia, Africa, Oceania",
                                            "holdings": {},
                                            },
                                            "Orchid Dottyback": {
                                            "common": "Orchid Dottyback",
                                            "scientific": "Pseudochromis fridmani",
                                            "info": "Probably the most well-known and commonly kept dottyback species, the orchid dottyback is endemic to the Red Sea. They shelter in holes and under overhangs on the reef, and can be distinguished from similar species from the black stripe on their face.",
                                            "type": "Fish",
                                            "order": "Blenniiformes",
                                            "family": "Pseudochromidae",
                                            "genus": "Pseudochromis",
                                            "image_url": "https://i.imgur.com/mwpIIOV.jpeg",
                                            "breeding": "Impossible",
                                            "region": "Asia, Africa",
                                            "holdings": {},
                                        },
                                        "Blue Damselfish": {
                                        "common": "Blue Damselfish",
                                        "scientific": "Chrysiptera cyanea",
                                        "info": "A popular aquarium species, the blue damselfish is a significantly more aggressive species than several others in its genus. They inhabit reefs and lagoons and feed on algae, tunicates, and copepods, which gives them their bright blue coloration.",
                                        "type": "Fish",
                                        "order": "Blenniiformes",
                                        "family": "Pomacentridae",
                                        "genus": "Chrysiptera",
                                        "image_url": "https://fishesofaustralia.net.au/Images/Image/ChrysiptCyaneaMAGNT.jpg",
                                        "breeding": "Impossible",
                                        "region": "Asia, Oceania",
                                        "holdings": {},
                                        },
                                        "Western Hognose Snake": {
                                        "common": "Western Hognose Snake",
                                        "scientific": "Heterodon nasicus",
                                        "info": "One of the most common and popular snakes in captivity, the western hognose snake can be found natively in the western part of North America, from Canada to Mexico. They primarily feed on amphibians in the wild, using their fangs to immobilize their prey.",
                                        "type": "Reptile",
                                        "order": "Squamata",
                                        "family": "Colubridae",
                                        "genus": "Heterodon",
                                        "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/4199357/large.jpg",
                                        "breeding": "Average",
                                        "region": "North America",
                                        "holdings": {},
                                        },
                                        "Madagascar Hissing Cockroach": {
                                        "common": "Madagascar Hissing Cockroach",
                                        "scientific": "Gromphadorhina portentosa",
                                        "info": "A large cockroach that is endemic to Madagascar, this species is known for its defensive behavior where it presses air through its body in order to make a hissing sound. They are hardy and popular in the pet trade. Their natural diet is mostly rotting fruits and plant matter.",
                                        "type": "Invertebrate",
                                        "order": "Blattodea",
                                        "family": "Blaberidae",
                                        "genus": "Gromphadorhina",
                                        "image_url": "https://static.inaturalist.org/photos/108698566/large.jpg",
                                        "breeding": "Very Easy",
                                        "region": "Africa",
                                        "holdings": {},
                                        },
                                        "Asian Forest Scorpion": {
                                        "common": "Asian Forest Scorpion",
                                        "scientific": "Heterometrus spinifer",
                                        "info": "A medium-sized scorpion reaching 4.7 inches in length, the Asian forest scorpion is native to the tropical rainforests of south and southeast Asia. They have become more popular in the pet trade recently due to their coloration and ease of care, as well as their size.",
                                        "type": "Invertebrate",
                                        "order": "Scorpiones",
                                        "family": "Scorpionidae",
                                        "genus": "Heterometrus",
                                        "image_url": "https://i.imgur.com/5aMyuLi.jpeg",
                                        "breeding": "Below Average",
                                        "region": "Asia",
                                        "holdings": {},
                                        },
                                        "Tokay Gecko": {
                                        "common": "Tokay Gecko",
                                        "scientific": "Gekko gecko",
                                        "info": "One of the largest gecko species in the world, the tokay gecko is a large, nocturnal species native to Asia and Oceania. They are aggressive and have been known to attack people, and they are voracious predators, feeding on invertebrates and small vertebrates.",
                                        "type": "Reptile",
                                        "order": "Squamata",
                                        "family": "Gekkonidae",
                                        "genus": "Gekko",
                                            "image_url": "https://i.imgur.com/JsLJtBn.jpeg",
                                            "breeding": "Average",
                                            "region": "Asia, Oceania",
                                            "holdings": {},
                                        },
                                        "Northern Curly-Tailed Lizard": {
                                        "common": "Northern Curly-Tailed Lizard",
                                        "scientific": "Leiocephalus carinatus",
                                        "info": "This species is also known as the saw-scaled curlytail. Originally native to the Caribbean, it is one of the most well-known invasive species in Florida. They are a small lizard, typically growing to 4 inches but in exceptional cases they will grow up to 10 inches.",
                                        "type": "Reptile",
                                        "order": "Squamata",
                                        "family": "Leiocephalidae",
                                        "genus": "Leiocephalus",
                                            "image_url": "https://i.imgur.com/OshVMxK.jpeg",
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                            },
                                            "Northern Pipefish": {
                                            "common": "Northern Pipefish",
                                            "scientific": "Syngnathus fuscus",
                                            "info": "A temperate-water pipefish that can be found from eastern Canada to the Gulf of Mexico, the northern pipefish is probably the most abundant pipefish within its range. They migrate seasonally and feed primarily on zooplankton in their coastal habitats.",
                                            "type": "Fish",
                                            "order": "Syngnathiformes",
                                            "family": "Syngnathidae",
                                            "genus": "Syngnathus",
                                                "image_url": "https://i.imgur.com/vcprtHG.jpeg",
                                                "breeding": "Below Average",
                                                "region": "North America",
                                                "holdings": {},
                                                    },

                                        "Eastern Newt": {
                                            "common": "Eastern Newt",
                                            "scientific": "Notophthalmus viridescens",
                                            "info": "A common newt found in eastern North America, the bright orange juvenile of this species is probably more well known than the more muted adults. These juveniles are called red efts, and their coloration advertises their poisonous nature to potential predators.",
                                            "type": "Amphibian",
                                            "order": "Urodela",
                                            "family": "Salamandridae",
                                            "genus": "Notophthalmus",
                                            "images": [
                                                {"label": "Red-spotted newt (viridescens)", "url": "https://i.imgur.com/9YvmnNd.jpeg"},
                                            ],
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                    },
                                        "Eastern Tiger Swallowtail": {
                                        "common": "Eastern Tiger Swallowtail",
                                        "scientific": "Papilio glaucus",
                                        "info": "One of the most common and recognizable butterflies of the eastern United States, the eastern tiger swallowtail ranges throughout the eastern and central part of the continent. There is both a yellow variant and a black variant, which can make it difficult to identify.",
                                        "type": "Invertebrate",
                                        "order": "Lepidoptera",
                                        "family": "Papilionidae",
                                        "genus": "Papilio",
                                            "images": [
                                                {"label": "Yellow variant", "url": "https://i.imgur.com/v5Ubb50.jpeg"},
                                            ],
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                        },
                                        "Uinta Ground Squirrel": {
                                        "common": "Uinta Ground Squirrel",
                                        "scientific": "Urocitellus armatus",
                                        "info": "A moderately sized ground squirrel endemic to the western United States, the Uinta ground squirrel prefers grassland habitats, living in loosely-associated colonies. They are active only a few times a year, spending the rest of the year hibernating.",
                                        "type": "Mammal",
                                        "order": "Rodentia",
                                        "family": "Sciuridae",
                                        "genus": "Urocitellus",
                                            "image_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/264824325/large.jpg",
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                        },
                                        "Texas Blind Salamander": {
                                        "common": "Texas Blind Salamander",
                                        "scientific": "Eurycea rathbuni",
                                        "info": "A highly endangered salamander endemic to the underground Edwards Aquifer of Texas. They are endangered due to habitat loss and pollution of their limited cave habitat, and are the center of a large captive breeding program throughout various zoos.",
                                        "type": "Amphibian",
                                        "order": "Urodela",
                                        "family": "Plethodontidae",
                                        "genus": "Eurycea",
                                            "image_url": "https://i.imgur.com/V4ukLvG.jpeg",
                                            "breeding": "Difficult",
                                            "region": "North America",
                                            "holdings": {},
                                        },
                                        "Steller's Jay": {
                                        "common": "Steller's Jay",
                                        "scientific": "Cyanocitta stelleri",
                                        "info": "This jay is endemic to the western part of the United States from southern Alaska to Nicaragua. They are a common and well-known bird in this part of the world, being adaptable omnivores that can survive in a wide variety of habitats.",
                                        "type": "Bird",
                                        "order": "Passeriformes",
                                        "family": "Corvidae",
                                        "genus": "Cyanocitta",
                                            "image_url": "https://i.imgur.com/CiCM6Qe.jpeg",
                                            "breeding": "Average",
                                            "region": "North America",
                                            "holdings": {},
                                                },
                                            "Common Box Turtle": {
                                                "common": "Common Box Turtle",
                                                "scientific": "Terrapene carolina",
                                                "info": "A species of box turtle with five distinct subspecies, the common box turtle can be found natively from Maine to southern Mexico. They are listed as Vulnerable on the IUCN Red List due to overcollection for the pet trade. They are the state reptile of North Carolina and Tennessee.",
                                                "type": "Reptile",
                                                "order": "Testudines",
                                                "family": "Emydidae",
                                                "genus": "Terrapene",
                                                "images": [
                                                    {"label": "Eastern box turtle (carolina)", "url": "https://i.imgur.com/lAQauwV.jpeg"},
                                                ],
                                                "breeding": "Below Average",
                                                "region": "North America",
                                                "holdings": {},
                                                },
                                        "Black Lion Tamarin": {
                                        "common": "Black Lion Tamarin",
                                        "scientific": "Leontopithecus chrysopygus",
                                        "info": "An endangered and rare tamarin endemic exclusively to the Brazilian state of São Paulo. They are a somewhat migratory species, alternating between different habitats. Their main threat is massive habitat loss in their native range, and a captive breeding program has been set up to safeguard the species.",
                                        "type": "Mammal",
                                        "order": "Primates",
                                        "family": "Callitrichidae",
                                        "genus": "Leontopithecus",
                                            "image_url": "https://i.imgur.com/KN7R2bP.jpeg",
                                            "breeding": "Below Average",
                                            "region": "South America",
                                            "holdings": {},
                                                },
                                        "Guianan Squirrel Monkey": {
                                        "common": "Guianan Squirrel Monkey",
                                        "scientific": "Saimiri sciureus",
                                        "info": "Once part of a large, continent-spanning species, a recent split has elevated the Guianan squirrel monkey of northeastern South America into its own species. Often moving in large, mixed-species groups with capuchin monkeys, the Guianan squirrel monkey is a highly social species of monkey with a complex social structure, with females cooperating to raise offspring.",
                                        "type": "Mammal",
                                        "order": "Primates",
                                        "family": "Cebidae",
                                        "genus": "Saimiri",
                                            "image_url": "https://i.imgur.com/EabQh75.jpeg",
                                            "breeding": "Average",
                                            "region": "South America",
                                            "holdings": {},
},
"North American Beaver": {
        "common": "North American Beaver",
        "scientific": "Castor canadensis",
        "info": "The North American beaver is the largest rodent in North America. A semi-aquatic forest engineer, they are well-known for their ability to divert river flow by cutting down trees with their sharp, always-growing teeth.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Castoridae",
        "genus": "Castor",
        "image_url": "https://objects.liquidweb.services/images/202203/inat_6244c541e2af69.37547492.jpg",
        "breeding": "Average",
        "region": "North America",
        "holdings": {
            "North America": "2.2 - Karl Eberhard's Pine Lake Zoo",},
},
"Great Horned Owl": {
        "common": "Great Horned Owl",
        "scientific": "Bubo virginianus",
        "info": "One of the largest owl species in the world, the great horned owl is widespread acros the Americas from Alaska to Argentina, and is a fearsome apex predator, so much so that early naturalists described it as the 'tiger of the air'.",
        "type": "Bird",
        "order": "Strigiformes",
        "family": "Strigidae",
        "genus": "Bubo",
        "image_url": "https://i.imgur.com/STAFgpQ.jpeg",
        "breeding": "Below Average",
        "region": "North America, South America",
        "holdings": {
            "North America": "0.1 - Karl Eberhard's Pine Lake Zoo",},
},
"Red Fox": {
    "common": "Red Fox",
    "scientific": "Vulpes vulpes",
    "info": "A large, iconic fox, the red fox is one of the most recognizable species of the Northern Hemisphere. An important mesopredator, they are highly adaptable and can be found in habitat ranging from untapped wilderness to developed areas.",
    "type": "Mammal",
    "order": "Carnivora",
    "family": "Canidae",
    "genus": "Vulpes",
    "image_url": "https://www.nrcm.org/wp-content/uploads/2018/12/Red-fox-winter-South-China-4-Hal-Winters.jpg",
    "breeding": "Average",
    "region": "North America, Europe, Asia, Africa",
    "holdings": {
        "North America": [
            "1.0 - Karl Eberhard's Pine Lake Zoo",
            "1.1 - Tri-State Zoo & Aquarium"
        ]
    }
},
"Peregrine Falcon": {
        "common": "Peregrine Falcon",
        "scientific": "Falco peregrinus",
        "info": "Regarded as the fastest animal on Earth, the peregrine falcon achieves this by diving from high altitudes at more than 200 mph to attack its prey. It was once heavily endangered due to DDT, but rebounded after a ban of the substance and captive breeding efforts.",
        "type": "Bird",
        "order": "Falconiformes",
        "family": "Falconidae",
        "genus": "Falco",
        "image_url": "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/303618951/900",
        "breeding": "Average",
        "region": "North America, South America, Europe, Asia, Africa, Oceania",
        "holdings": {
            "North America": "1.1 - Karl Eberhard's Pine Lake Zoo",},
},
"Black-Tailed Prairie Dog": {
        "common": "Black-Tailed Prairie Dog",
        "scientific": "Cynomys ludovicianus",
        "info": "A species of ground squirrel native to North America, the black-tailed prairie dog was formerly one of the most abundant mammals on Earth. Formerly numbering in the hundreds of millions, they have undergone a steep population decline, but are still regarded as Least Concern on the IUCN Red List.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Sciuridae",
        "genus": "Cynomys",
        "image_url": "https://i.imgur.com/BBgOdJf.jpeg",
        "breeding": "Easy",
        "region": "North America",
        "holdings": {
            "North America": "4.4 - Karl Eberhard's Pine Lake Zoo",},
},
"Klipspringer": {
    "common": "Klipspringer",
    "scientific": "Oreotragus oreotragus",
    "info": "The klipspringer is a small, distinctive rock-climbing antelope native to southern and eastern Africa. They are abundant and common, and occur in several protected areas. They can live in a variety of habitats including savannahs and deserts.",
    "type": "Mammal",
    "order": "Artiodactyla",
    "family": "Bovidae",
    "genus": "Oreotragus",
    "image_url": "https://i.imgur.com/Hrp9mOg.jpeg",
    "breeding": "Below Average",
    "region": "Africa",
    "holdings": {
        "North America": [
            "1.2 - Riverside Zoo",
            "1.1 - Tri-State Zoo & Aquarium"
        ]
    }
},
"Emu": {
    "common": "Emu",
    "scientific": "Dromaius novaehollandiae",
    "info": "The third-largest of living birds, the emu is native to mainland Australia. Remarkably hardy and adaptable, it is a species with a complex, loose social structure. They are one of Australia's most well-known and beloved animals.",
    "type": "Bird",
    "order": "Casuariiformes",
    "family": "Casuariidae",
    "genus": "Dromaius",
    "image_url": "https://i.imgur.com/IACeQul.jpeg",
    "breeding": "Average",
    "region": "Oceania",
    "holdings": {
        "North America": "1.1 - Riverside Zoo",
        "Europe": [
            "1.1 - Parque Zoologico de Clear Coast"
        ]
    }
},
"Red Kangaroo": {
        "common": "Red Kangaroo",
        "scientific": "Osphranter rufus",
        "info": "The largest extant marsupial, the red kangaroo is native to dry areas of Australia, and is one of the country's most iconic megafauna. They generally live in small groups, typically consisting of females and their offspring, but large aggregations occur on occasion.",
        "type": "Mammal",
        "order": "Diprotodontia",
        "family": "Macropodidae",
        "genus": "Osphranter",
        "image_url": "https://i.imgur.com/8W9QXDi.jpeg",
        "breeding": "Average",
        "region": "Oceania",
        "holdings": {
            "North America": "2.3 - Riverside Zoo",
            "Europe": [
                "1.3 - Brussels Zoo",
            ]
        },
},
"Hamerkop": {
        "common": "Hamerkop",
        "scientific": "Scopus umbretta",
        "info": "A distinct stork-like bird native to sub-Saharan Africa and the Arabian Peninsula, the hamerkop's taxonomic status was long unresolved, but it is believed to be related to the ibises and herons. They are a predatory species, hunting small animals such as fish and frogs.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Scopidae",
        "genus": "Scopus",
        "image_url": "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/218185311/900",
        "breeding": "Below Average",
        "region": "Asia, Africa",
        "holdings": {
            "North America": "1.0 - Riverside Zoo",
            "Europe": [
                "0.2 - Brussels Zoo",
                "1.1 - Parque Zoologico de Clear Coast"
            ]
        },
},
"Grévy's Zebra": {
        "common": "Grévy's Zebra",
        "scientific": "Equus grevyi",
        "info": "The largest and most endangered of the 3 extant zebra species, the Grévy's zebra is endemic to small patches of East Africa. This species was named after the French president Jules Grévy, who was gifted one by the government of Ethiopia, leading to its discovery by western science.",
        "type": "Mammal",
        "order": "Perissodactyla",
        "family": "Equidae",
        "genus": "Equus",
        "image_url": "https://i.imgur.com/IqWARwu.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "North America": "0.3 - Riverside Zoo",
            "Europe": [
                "0.3 - Brussels Zoo",]
        },
                                },
"Capybara": {
    "common": "Capybara",
    "scientific": "Hydrochoerus hydrochaeris",
    "info": "The capybara is the largest rodent species alive today, and is one of the most famous animals of South America. Having become recently internet famous, these large caviids are well-known for their social nature and mellow temperament.",
    "type": "Mammal",
    "order": "Rodentia",
    "family": "Caviidae",
    "genus": "Hydrochoerus",
    "image_url": "https://i.imgur.com/RPxqT9I.jpeg",
    "breeding": "Easy",
    "region": "South America",
    "holdings": {
        "North America": [
            "1.2 - Riverside Zoo",
            "3.5 - Tri-State Zoo & Aquarium"
        ],
        "Europe": [
            "5.5 - Parque Zoologico de Clear Coast"
        ]
    }
},
"Common Warthog": {
        "common": "Common Warthog",
        "scientific": "Phacochoerus africanus",
        "info": "A famous wild pig native to Sub-Saharan Africa, the common warthog is a highly adaptable and very widespread species, ranging from Mauritania to South Africa. They are omnivorous foragers, and have been known to allow mongooses and monkeys to clean them of parasites, an example of mutualism.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Suidae",
        "genus": "Phacochoerus",
        "image_url": "https://i.imgur.com/yBWLu3I.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "North America": "1.2 - Riverside Zoo",
            "Europe": [
                "1.0 - Brussels Zoo",
]        },
},
"Green Araçari": {
        "common": "Green Araçari",
        "scientific": "Pteroglossus viridis",
        "info": "The green araçari is a small relative of the toucans. Native to the Guiana Shield subregion of the Amazon rainforest, they are commonly found in both zoos and in the private trade. They feed mostly on fruit and nest within Cecropia and palm trees.",
        "type": "Bird",
        "order": "Piciformes",
        "family": "Ramphastidae",
        "genus": "Pteroglossus",
        "image_url": "https://i.imgur.com/iuFxuPA.jpeg",
        "breeding": "Average",
        "region": "South America",
        "holdings": {
            "North America": "1.2 - Riverside Zoo",},
},
"Greater Prairie Chicken": {
        "common": "Greater Prairie Chicken",
        "scientific": "Tympanuchus cupido",
        "info": "A large grouse native to central North America, the greater prairie chicken has undergone a preciptious decline since the arrival of humans to the area. There are three subspecies, one of which is extinct, the other critically endangered, and the third near threatened.",
        "type": "Bird",
        "order": "Galliformes",
        "family": "Phasianidae",
        "genus": "Tympanuchus",
        "image_url": "https://i.imgur.com/4v5vDep.jpeg",
        "breeding": "Difficult",
        "region": "North America",
        "holdings": {
            "North America": "1.2 - Riverside Zoo",},
},
"Greater Flamingo": {
    "common": "Greater Flamingo",
    "scientific": "Phoenicopterus roseus",
    "info": "The largest and most widespread flamingo species, the greater flamingo is native to the Old World, from southern Europe all the way east to the Indian subcontinent and into sub-Saharan Africa. They feed on plankton using their specialized beaks, with their pinkish color coming from the astaxanthin in the food they eat.",
    "type": "Bird",
    "order": "Phoenicopteriformes",
    "family": "Phoenicopteridae",
    "genus": "Phoenicopterus",
    "image_url": "https://i.imgur.com/ligKWZZ.jpeg",
    "breeding": "Average",
    "region": "Europe, Asia, Africa",
    "holdings": {
        "North America": [
            "5.5 - Las Vegas Zoo"
        ],
        "Europe": [
            "5.5 - Brussels Zoo",
            "10.10 - Parque Zoologico de Clear Coast"
        ]
    }
},
"African Crested Porcupine": {
        "common": "African Crested Porcupine",
        "scientific": "Hystrix cristata",
        "info": "A large porcupine native to southern Europe and Africa, the African crested porcupine is well-known for its long quills, which dissuade predators from attacking it. It is a long-lived and hardy rodent, with the maximum recorded lifespan being 28 years.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Hystricidae",
        "genus": "Hystrix",
        "image_url": "https://i.imgur.com/N8EvIbs.jpeg",
        "breeding": "Easy",
        "region": "Europe, Africa",
        "holdings": {
            "Europe": "0.2 - Brussels Zoo",},
},
"Common Ostrich": {
        "common": "Common Ostrich",
        "scientific": "Struthio camelus",
        "info": "The largest of all living birds, the common ostrich is native to sub-Saharan Africa and formerly the Middle East. An iconic and well-known species, the common ostrich is sometimes utilized by humans for their eggs and meat.",
        "type": "Bird",
        "order": "Struthioniformes",
        "family": "Struthionidae",
        "genus": "Struthio",
        "image_url": "https://i.imgur.com/Qlo9BZU.png",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "Europe": "1.2 - Brussels Zoo",},
},
"Aardvark": {
    "common": "Aardvark",
    "scientific": "Orycteropus afer",
    "info": "This unique mammal is one of the closest living relatives of modern elephants. Native to most of Africa, the aardvark is a nocturnal insectivore, renowned for its digging ability and sense of smell. Its common name is the Afrikaans word 'earth pig'.",
    "type": "Mammal",
    "order": "Tubulidentata",
    "family": "Orycteropodidae",
    "genus": "Orycteropus",
    "image_url": "https://i.imgur.com/LQZkwJK.jpeg",
    "breeding": "Below Average",
    "region": "Africa",
    "holdings": {
        "Europe": [
            "1.1 - Brussels Zoo",
            "1.1.2.0 - Parque Zoologico de Clear Coast"
        ]
    }
},
"Tammar Wallaby": {
        "common": "Tammar Wallaby",
        "scientific": "Notamacropus eugenii",
        "info": "Though its range has been much reduced since European colonization of Australia, the tammar wallaby is not threatened nor is it uncommon in its small, disjunct range. It has several unique adaptations, such as full spectrum color vision and the ability to drink saltwater.",
        "type": "Mammal",
        "order": "Diprotodontia",
        "family": "Macropodidae",
        "genus": "Notamacropus",
        "image_url": "https://i.imgur.com/AjG6O5R.jpeg",
        "breeding": "Average",
        "region": "Oceania",
        "holdings": {
            "Europe": "1.1 - Brussels Zoo",},
},
"Laughing Kookaburra": {
        "common": "Laughing Kookaburra",
        "scientific": "Dacelo novaeguineae",
        "info": "One of the world's largest kingfishers, the laughing kookaburra is one of Australia's most famous birds due to its iconic call and distinctive morphology. Unlike most kingfishers, it feeds primarily on terrestrial animals such as lizards and rodents.",
        "type": "Bird",
        "order": "Coraciiformes",
        "family": "Alcedinidae",
        "genus": "Dacelo",
        "image_url": "https://i.imgur.com/fltFUog.jpeg",
        "breeding": "Average",
        "region": "Oceania",
        "holdings": {
            "Europe": "1.2 - Brussels Zoo",},
},
"Masked Lapwing": {
        "common": "Masked Lapwing",
        "scientific": "Vanellus miles",
        "info": "A large, common lapwing endemic to Oceania, the masked lapwing is an adaptable species that resides in a wide variety of habitats within its range, including human-inhabited areas, where it will make a nest on just about any suitable flat surface it can.",
        "type": "Bird",
        "order": "Charadriiformes",
        "family": "Charadriidae",
        "genus": "Vanellus",
        "image_url": "https://i.imgur.com/tQjYRTO.jpeg",
        "breeding": "Average",
        "region": "Oceania",
        "holdings": {
            "Europe": "1.1 - Brussels Zoo",},
},
"Marsh Deer": {
        "common": "Marsh Deer",
        "scientific": "Blastocerus dichotomus",
        "info": "The largest South American deer, the marsh deer is regarded as a vulnerable species due to the loss of its valuable wetlands habitat, which is being increasingly destroyed for ranching and development.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Cervidae",
        "genus": "Blastocerus",
        "image_url": "https://i.imgur.com/ERDJ5Ns.jpeg",
        "breeding": "Below Average",
        "region": "South America",
        "holdings": {
            "Europe": "1.4 - Parque Zoologico de Clear Coast",},
},
"White-Faced Ibis": {
        "common": "White-Faced Ibis",
        "scientific": "Plegadis chihi",
        "info": "A widespread species of ibis, the white-faced ibis can be found in both North and South America, typically in marshes. They nest in low trees or bushes, and are closely related to the more well-known glossy ibis, at one point thought to be a subspecies of them.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Threskiornithidae",
        "genus": "Plegadis",
        "image_url": "https://i.imgur.com/mceYutp.jpeg",
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {
            "Europe": "4.4 - Parque Zoologico de Clear Coast",},
},
"Scarlet Ibis": {
        "common": "Scarlet Ibis",
        "scientific": "Eudocimus ruber",
        "info": "A colorful ibis native to the Caribbean and northern South America, the scarlet ibis gets its unique red coloration from the astaxanthin in the small crustaceans they eat, much like flamingos. They live in large flocks of sometimes thousands of individuals.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Threskiornithidae",
        "genus": "Eudocimus",
        "image_url": "https://i.imgur.com/AR4ADIn.jpeg",
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {
            "Europe": "7.7 - Parque Zoologico de Clear Coast",},
},
"Common Kingfisher": {
        "common": "Common Kingfisher",
        "scientific": "Alcedo atthis",
        "info": "This small, widespread kingfisher can be found across multiple continents, from Ireland to the Solomon Islands. It is a rapacious piscivore, with excellent vision allowing it to capture its preferred prey. They have a high metabolism and must eat 60% of their body weight every day to survive.",
        "type": "Bird",
        "order": "Coraciiformes",
        "family": "Alcedinidae",
        "genus": "Alcedo",
        "image_url": "https://i.imgur.com/GWTCeio.png",
        "breeding": "Difficult",
        "region": "Europe, Asia, Africa, Oceania",
        "holdings": {
            "Europe": "1.1 - Parque Zoologico de Clear Coast",},
},
"Nile Lechwe": {
        "common": "Nile Lechwe",
        "scientific": "Kobus megaceros",
        "info": "An endangered antelope endemic to east Africa, the Nile lechwe is a swamp specialist, preferring wetland and marsh habitat to open savannah. They have significant sexual dimorphism, with males possessing horns and a different fur color from females.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Bovidae",
        "genus": "Kobus",
        "image_url": "https://i.imgur.com/juOEAam.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "Europe": "1.3 - Parque Zoologico de Clear Coast",},
},
"Sarus Crane": {
        "common": "Sarus Crane",
        "scientific": "Antigone antigone",
        "info": "The sarus crane is found in tropical Asia and Australia. A large crane species, they are a culturally significant species to humans, especially in India, where they are borderline revered. This has helped them in some regards conservation-wise, but they are still listed as Vulnerable on the IUCN Red List.",
        "type": "Bird",
        "order": "Gruiformes",
        "family": "Gruidae",
        "genus": "Antigone",
        "image_url": "https://i.imgur.com/SDc9qXS.jpeg",
        "breeding": "Difficult",
        "region": "Asia, Oceania",
        "holdings": {
            "Europe": "2.2 - Parque Zoologico de Clear Coast",},
},
"Egyptian Goose": {
        "common": "Egyptian Goose",
        "scientific": "Alopochen aegyptiaca",
        "info": "The Egyptian goose, native to sub-Saharan Africa, is one of the most common waterfowl in captivity, due to its popularity as an ornamental bird. Remarkably aggressive for a waterfowl, they were often depicted in ancient Egyptian art.",
        "type": "Bird",
        "order": "Anseriformes",
        "family": "Anatidae",
        "genus": "Alopochen",
        "image_url": "https://i.imgur.com/8XAaabC.jpeg",
        "breeding": "Easy",
        "region": "Africa",
        "holdings": {
            "Europe": "3.3 - Parque Zoologico de Clear Coast",},
},
"Common Shelduck": {
        "common": "Common Shelduck",
        "scientific": "Tadorna tadorna",
        "info": "A widespread shelduck native to the Old World, the common shelduck has a degree of sexual dimorphism, where the males have a knobby crest on their bill. They will sometimes gather in huge flocks during times of vulnerability like molting.",
        "type": "Bird",
        "order": "Anseriformes",
        "family": "Anatidae",
        "genus": "Tadorna",
        "image_url": "https://i.imgur.com/ERAnMz7.jpeg",
        "breeding": "Average",
        "region": "Europe, Asia, Africa",
        "holdings": {
            "Europe": "3.3 - Parque Zoologico de Clear Coast",},
},
"Little Egret": {
        "common": "Little Egret",
        "scientific": "Egretta garzetta",
        "info": "A small egret that is extremely widespread, the little egret is a social bird that will aggregate in huge flocks with other species during the breeding season. It is one of several species that has begun to colonize outside of its native range, being sighted in the Caribbean in 1954.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Ardeidae",
        "genus": "Egretta",
        "image_url": "https://i.imgur.com/CllSIyT.jpeg",
        "breeding": "Average",
        "region": "Europe, Asia, Africa, Oceania",
        "holdings": {
            "Europe": "4.4 - Parque Zoologico de Clear Coast",},
},
"Leopard Shark": {
    "common": "Leopard Shark",
    "scientific": "Triakis semifasciata",
    "info": "The most common and well-known houndshark, the leopard shark is native to the northeastern Pacific, from Washington to central Mexico. Feeding on small animals, they are commonly found in mudflat and kelp forest environments.",
    "type": "Fish",
    "order": "Carcharhiniformes",
    "family": "Triakidae",
    "genus": "Triakis",
    "image_url": "https://i.imgur.com/VKUsf3U.jpeg",
    "breeding": "Difficult",
    "region": "North America",
    "holdings": {}
},
"Garibaldi": {
    "common": "Garibaldi",
    "scientific": "Hypsypops rubicundus",
    "info": "The largest damselfish species, the garibaldi is native to the northeastern Pacific, and is the official state marine fish of California. An aggressive, territorial species, garibaldis start off with blue spots on their scales, but soon turn into a full orange color when fully grown.",
    "type": "Fish",
    "order": "Blenniiformes",
    "family": "Pomacentridae",
    "genus": "Hypsypops",
    "image_url": "https://i.imgur.com/DkPKxmL.jpeg",
    "breeding": "Impossible",
    "region": "North America",
    "holdings": {}
},
"Cortez Angelfish": {
    "common": "Cortez Angelfish",
    "scientific": "Pomacanthus zonipectus",
    "info": "An angelfish endemic to the eastern Pacific, the Cortez angelfish, like most marine angelfish, undergoes a significant metamorphosis from its juvenile to adult forms. Feeding mostly on sessile marine invertebrates, they are found in reef habitats, and juveniles have been reported to live in tidepools as well.",
    "type": "Fish",
    "order": "Acanthuriformes",
    "family": "Pomacanthidae",
    "genus": "Pomacanthus",
    "image_url": "https://i.imgur.com/cYYIfWK.jpeg",
    "breeding": "Impossible",
    "region": "North America, South America",
    "holdings": {}
},
"King Angelfish": {
    "common": "King Angelfish",
    "scientific": "Holacanthus passer",
    "info": "Endemic to the east Pacific, the king angelfish is probably the most well-known species of angelfish from this region. Found from Mexico to Ecuador and the Galapagos Islands, they are a carnivorous species like most angelfish, and have been known to take part in a mutualistic cleaning relationship with certain damselfish species.",
    "type": "Fish",
    "order": "Acanthuriformes",
    "family": "Pomacanthidae",
    "genus": "Holacanthus",
    "image_url": "https://i.imgur.com/A3hKcyA.jpeg",
    "breeding": "Impossible",
    "region": "North America, South America",
    "holdings": {}
},
"Clarion Angelfish": {
    "common": "Clarion Angelfish",
    "scientific": "Holacanthus clarionensis",
    "info": "The Clarion angelfish is endemic to a few small islands off the coast of Mexico, with vagrants occasionally being reported from elsewhere. A prized species in the aquarium trade, Clarion angelfish are heavily regulated due to their vulnerable status, and only a few are collected each year for this purpose.",
    "type": "Fish",
    "order": "Acanthuriformes",
    "family": "Pomacanthidae",
    "genus": "Holacanthus",
    "image_url": "https://i.imgur.com/NmbRUXq.jpeg",
    "breeding": "Impossible",
    "region": "North America",
    "holdings": {}
},
"Paddlefin Wrasse": {
    "common": "Paddlefin Wrasse",
    "scientific": "Thalassoma lucasanum",
    "info": "Also known as the Cortez rainbow wrasse, the paddlefin wrasse is a medium-sized wrasse native to the east Pacific, from Baja California to Peru, as well as the Galapagos Islands. It is sometimes collected for the aquarium trade, but is regarded as boisterous and aggressive.",
    "type": "Fish",
    "order": "Labriformes",
    "family": "Labridae",
    "genus": "Thalassoma",
    "image_url": "https://i.imgur.com/Hq2tn3P.jpeg",
    "breeding": "Impossible",
    "region": "North America, South America",
    "holdings": {}
},
"Haller's Round Ray": {
    "common": "Haller's Round Ray",
    "scientific": "Urobatis halleri",
    "info": "A small stingray native to the northeastern Pacific, the Haller's round ray is a common sight from California to Panama. Residing in shallow water, they are often implicated in attacks on humans, where someone steps on them accidentally, and the stingray reacts by defensively stabbing them with its venomous barb.",
    "type": "Fish",
    "order": "Myliobatiformes",
    "family": "Urotrygonidae",
    "genus": "Urobatis",
    "image_url": "https://i.imgur.com/lJ8t9q7.jpeg",
    "breeding": "Difficult",
    "region": "North America",
    "holdings": {}
},
"Shovelnose Guitarfish": {
    "common": "Shovelnose Guitarfish",
    "scientific": "Pseudobatos productus",
    "info": "One of the smaller guitarfish species, the shovelnose guitarfish is native to a small stretch of the eastern Pacific coastline from California to Mexico. Residing in shallow estuaries and bays, they are a carnivorous species, feeding mainly on benthic invertebrates and small fish.",
    "type": "Fish",
    "order": "Rhinopristiformes",
    "family": "Rhinobatidae",
    "genus": "Pseudobatos",
    "image_url": "https://i.imgur.com/2whQodp.jpeg",
    "breeding": "Difficult",
    "region": "North America",
    "holdings": {}
},
"Horn Shark": {
    "common": "Horn Shark",
    "scientific": "Heterodontus francisci",
    "info": "A bullhead shark endemic to the east Pacific in California and Mexico, the horn shark is one of the better-known species of its order. Its distinctive horn, located just before the dorsal fin, gives it its common name. They are sometimes collected for the aquarium trade and are often displayed in public aquariums.",
    "type": "Fish",
    "order": "Heterodontiformes",
    "family": "Heterodontidae",
    "genus": "Heterodontus",
    "image_url": "https://i.imgur.com/lxWjdw2.jpeg",
    "breeding": "Difficult",
    "region": "North America",
    "holdings": {}
},
"Panama Porkfish": {
    "common": "Panama Porkfish",
    "scientific": "Anisotremus taeniatus",
    "info": "Closely related to the more well-known porkfish of the Caribbean, the Panama porkfish can be distinguished from this species through the blue stripes on the side of its body. They can be found from Mexico to Peru in the east Pacific, and are a schooling mesopredator, hunting small animals on the reef.",
    "type": "Fish",
    "order": "Acanthuriformes",
    "family": "Haemulidae",
    "genus": "Anisotremus",
    "image_url": "https://i.imgur.com/nx04qaU.jpeg",
    "breeding": "Impossible",
    "region": "North America, South America",
    "holdings": {}
},
"Blacktip Reef Shark": {
    "common": "Blacktip Reef Shark",
    "scientific": "Carcharhinus melanopterus",
    "info": "The blacktip reef shark is one of the most recognizable and common sharks in aquaria. Native to the Indo-Pacific from the Red Sea to the central Pacific, this shark species does not grow very large, typically growing to about 5ft in length. They are the only requiem shark bred with some regularity in captivity.",
    "type": "Fish",
    "order": "Carcharhiniformes",
    "family": "Carcharhinidae",
    "genus": "Carcharhinus",
    "image_url": "https://i.imgur.com/097tZHD.jpeg",
    "breeding": "Difficult",
    "region": "Asia, Africa, Oceania",
    "holdings": {}
},
"Longnose Hawkfish": {
    "common": "Longnose Hawkfish",
    "scientific": "Oxycirrhites typus",
    "info": "Found on tropical reefs in the Indian and Pacific oceans, the longnose hawkfish is a common sight in the aquarium trade. Typically found in deeper water than most hawkfish, they are a carnivorous species, feeding on small crustaceans, and are well-known for eating cleaner shrimp in home aquariums.",
    "type": "Fish",
    "order": "Centrarchiformes",
    "family": "Cirrhitidae",
    "genus": "Oxycirrhites",
    "image_url": "https://i.imgur.com/E6EEMuo.jpeg",
    "breeding": "Impossible",
    "region": "North America, South America, Asia, Africa, Oceania",
    "holdings": {}
},
"Black Durgon": {
    "common": "Black Durgon",
    "scientific": "Melichthys niger",
    "info": "Known as humuhumu'ele'ele in the Hawaiian language, the black durgon is a widespread species of triggerfish native to tropical seas globally, including both the Caribbean and Indo-Pacific. They are capable of changing color based on their surroundings, and feed upon small animals and plankton.",
    "type": "Fish",
    "order": "Tetraodontiformes",
    "family": "Balistidae",
    "genus": "Melichthys",
    "image_url": "https://i.imgur.com/hX8lDgj.jpeg",
    "breeding": "Impossible",
    "region": "North America, South America, Asia, Africa, Oceania",
    "holdings": {}
},
"Pinktail Triggerfish": {
    "common": "Pinktail Triggerfish",
    "scientific": "Melichthys vidua",
    "info": "A medium-sized triggerfish native to the Indian Ocean, the pinktail triggerfish is regarded as a more peaceful and amicable species than many of its fellow triggerfish, making it a popular aquarium species. They feed on, unusually for a triggerfish, primarily plant matter and plankton.",
    "type": "Fish",
    "order": "Tetraodontiformes",
    "family": "Balistidae",
    "genus": "Melichthys",
    "image_url": "https://i.imgur.com/25YIRZ2.jpeg",
    "breeding": "Impossible",
    "region": "Asia, Africa, Oceania",
    "holdings": {}
},
"Western Sandpiper": {
    "common": "Western Sandpiper",
    "scientific": "Calidris mauri",
    "info": "A small sandpiper found in the Americas and Siberia, the western sandpiper is most closely related to the semipalmated sandpiper. They primarily feed on intertidal biofilm, and both parents participate in nesting and raising the young.",
    "type": "Bird",
    "order": "Charadriiformes",
    "family": "Scolopacidae",
    "genus": "Calidris",
    "image_url": "https://i.imgur.com/N0Y3qOp.jpeg",
    "breeding": "Below Average",
    "region": "North America, South America, Asia",
    "holdings": {}
},
"Snowflake Moray": {
    "common": "Snowflake Moray",
    "scientific": "Echidna nebulosa",
    "info": "A medium-sized eel native to the Indo-Pacific, the snowflake moray is one of several moray species specialized to hunt crustaceans rather than fish. Their teeth are more blunted and used for crushing rather than piercing, which makes them popular in home aquaria due to a lack of eating potential tankmates.",
    "type": "Fish",
    "order": "Anguilliformes",
    "family": "Muraenidae",
    "genus": "Echidna",
    "image_url": "https://i.imgur.com/AGhRnwA.jpeg",
    "breeding": "Impossible",
    "region": "North America, South America, Asia, Africa, Oceania",
    "holdings": {}
},
"Major Mitchell's Cockatoo": {
        "common": "Major Mitchell's Cockatoo",
        "scientific": "Cacatua leadbeateri",
        "info": "Major Mitchell's cockatoo, also known as the pink cockatoo or Leadbeater's cockatoo, is a medium-sized cockatoo native to central and eastern Australia. They have declined in population in recent years due to extensive development of their preferred habitats.",
        "type": "Bird",
        "order": "Psittaciformes",
        "family": "Cacatuidae",
        "genus": "Cacatua",
        "image_url": "https://i.imgur.com/ufe8MnA.jpeg",
        "breeding": "Below Average",
        "region": "Oceania",
        "holdings": {
            "Europe": "1.0 - Brussels Zoo",},
},
"Rock Ptarmigan": {
        "common": "Rock Ptarmigan",
        "scientific": "Lagopus muta",
        "info": "A medium-sized grouse native to alpine and boreal regions of the Northern Hemisphere, the rock ptarmigan is an adaptable and widespread species that can be found on multiple continents. Their distinctive cry is loud and booming, giving them the common name 'thunder bird' in Japan.",
        "type": "Bird",
        "order": "Galliformes",
        "family": "Phasianidae",
        "genus": "Lagopus",
        "image_url": "https://i.imgur.com/oMXLehR.jpeg",
        "breeding": "Below Average",
        "region": "North America, Europe, Asia",
        "holdings": {
            "North America": "1.2 - Karl Eberhard's Pine Lake Zoo",},
},
"King Vulture": {
        "common": "King Vulture",
        "scientific": "Sarcoramphus papa",
        "info": "A large, distinctive vulture native to Central and South America, the king vulture is an important part of the ecosystem, scavenging on carrion and roadkill left behind by other animals and people. In indigenous culture they are a highly respected bird, and feature prominently in local folklore.",
        "type": "Bird",
        "order": "Accipitriformes",
        "family": "Cathartidae",
        "genus": "Sarcoramphus",
        "image_url": "https://i.imgur.com/a5pwO2G.jpeg",
        "breeding": "Below Average",
        "region": "North America, South America",
        "holdings": {
            "Europe": "2.2 - Parque Zoologico de Clear Coast",},
},
"Green Moray": {
        "common": "Green Moray",
        "scientific": "Gymnothorax funebris",
        "info": "One of the largest moray eel species, the green moray can be found in the western Atlantic ocean from New York to Brazil, at depths of down to 130ft. This species's common name comes from its mucus coating, which if removed, actually makes it appear more dark, rather than green.",
        "type": "Fish",
        "order": "Anguilliformes",
        "family": "Muraenidae",
        "genus": "Gymnothorax",
        "image_url": "https://i.imgur.com/JKR8vCn.png",
        "breeding": "Impossible",
        "region": "North America, South America",
        "holdings": {
            "North America": "1 - Tri-State Zoo & Aquarium",},
},
"Blackbar Soldierfish": {
        "common": "Blackbar Soldierfish",
        "scientific": "Myripristis jacobus",
        "info": "A soldierfish from the West Atlantic, the blackbar soldierfish is a nocturnal mesopredator that schools for protection from other predators. They feed mainly on plankton and shrimp, and are sometimes found in the aquarium trade and public aquariums as display fish.",
        "type": "Fish",
        "order": "Beryciformes",
        "family": "Holocentridae",
        "genus": "Myripristis",
        "image_url": "https://i.imgur.com/RY7f5ix.jpeg",
        "breeding": "Impossible",
        "region": "North America, South America",
        "holdings": {
            "North America": "6 - Tri-State Zoo & Aquarium",},
},
"Spanish Hogfish": {
        "common": "Spanish Hogfish",
        "scientific": "Bodianus rufus",
        "info": "A medium-sized wrasse native to the Caribbean, the Spanish hogfish starts out its life as a brightly-colored juvenile, but as it matures, the colors dull. While some reach up to 16 inches, most do not exceed 11 inches. It is commonly collected for the aquarium trade and for public aquariums.",
        "type": "Fish",
        "order": "Labriformes",
        "family": "Labridae",
        "genus": "Bodianus",
        "image_url": "https://i.imgur.com/nJY8Tq1.jpeg",
        "breeding": "Impossible",
        "region": "North America, South America",
        "holdings": {
            "North America": "4 - Tri-State Zoo & Aquarium",},
},
"Bluegill": {
    "common": "Bluegill",
    "scientific": "Lepomis macrochirus",
    "info": "Perhaps the most well known of the sunfish, the bluegill is a common and popular sport fish throughout North America and its introduced range, which includes Europe and Japan. They have a complex mating system for a fish, which includes 'sleeper males' who try to mate with females outside of the watch of the dominant male.",
    "type": "Fish",
    "order": "Centrarchiformes",
    "family": "Centrarchidae",
    "genus": "Lepomis",
    "image_url": "https://i.imgur.com/tzR2Ofj.jpeg",
    "breeding": "Difficult",
    "region": "North America",
    "holdings": {
        "North America": [
            "6 - Chicago City Aquarium",
            "6 - Tri-State Zoo & Aquarium"
        ]
    }
},
"Gopher Tortoise": {
        "common": "Gopher Tortoise",
        "scientific": "Gopherus polyphemus",
        "info": "A species of tortoise native to the southeastern United States, the gopher tortoise is an excellent example of a keystone species. Its burrows are used by a variety of species, whether the tortoise is in there or not, and can provide shelter during natural disasters such as wildfires.",
        "type": "Reptile",
        "order": "Testudines",
        "family": "Testudinidae",
        "genus": "Gopherus",
        "image_url": "https://i.imgur.com/ijW4cJG.jpeg",
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {
            "North America": "2.2 - Tri-State Zoo & Aquarium",},
},
"Spiny Softshell Turtle": {
        "common": "Spiny Softshell Turtle",
        "scientific": "Apalone spinifera",
        "info": "One of the largest freshwater turtles in North America, the spiny softshell turtle is a fast-moving carnivorous species that can be found from Canada to Mexico. They are divided into six subspecies, some of which are quite rare or endangered.",
        "type": "Reptile",
        "order": "Testudines",
        "family": "Trionychidae",
        "genus": "Apalone",
        "image_url": "https://i.imgur.com/IjmLk11.jpeg",
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {
            "North America": "0.2 - Tri-State Zoo & Aquarium",},
},
"Smallmouth Bass": {
    "common": "Smallmouth Bass",
    "scientific": "Micropterus dolomieu",
    "info": "A common and widely distributed centrarchid, the smallmouth bass is a popular gamefish wherever it is found, famed for its outsized strength. They live in rocky environments with plentiful prey, and feed mainly on small animals such as tadpoles, crayfish, and smaller fish.",
    "type": "Fish",
    "order": "Centrarchiformes",
    "family": "Centrarchidae",
    "genus": "Micropterus",
    "image_url": "https://i.imgur.com/aatHbau.jpeg",
    "breeding": "Difficult",
    "region": "North America",
    "holdings": {
        "North America": [
            "4 - Chicago City Aquarium",
            "2 - Tri-State Zoo & Aquarium"
        ]
    },
},
"Cottonmouth": {
        "common": "Cottonmouth",
        "scientific": "Agkistrodon piscivorus",
        "info": "The cottonmouth is a semi-aquatic pit viper species native to temperate and subtropical North America. Its common names comes from its distinctive white mouth, which is used for threat displays against potential predators.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Agkistrodon",
        "image_url": "https://i.imgur.com/l5rLbH8.jpeg",
        "breeding": "Below Average",
        "region": "North America",
        "holdings": {
            "North America": "0.1 - Tri-State Zoo & Aquarium",},
},
"American Red Squirrel": {
        "common": "American Red Squirrel",
        "scientific": "Tamiasciurus hudsonicus",
        "info": "One of the most common squirrel species in North America, the American red squirrel resides in a large swath of land from Alaska to Mexico and the Eastern Seaboard. They are adaptable and their range is expanding into non-prime habitat.",
        "type": "Mammal",
        "order": "Rodentia",
        "family": "Sciuridae",
        "genus": "Tamiasciurus",
        "image_url": "https://i.imgur.com/vMiaFan.jpeg",
        "breeding": "Average",
        "region": "North America",
        "holdings": {
            "North America": "1.1 - Tri-State Zoo & Aquarium",},
},
"Green Anole": {
        "common": "Green Anole",
        "scientific": "Anolis carolinensis",
        "info": "A common, widespread, and adaptable tree-dwelling lizard, the green anole is native to the southeastern United States but has been introduced to the Caribbean and Oceania. Able to change color, it is sometimes referred to as the American chameleon in the pet trade because of this.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Dactyloidae",
        "genus": "Anolis",
        "image_url": "https://i.imgur.com/bL0wm5d.jpeg",
        "breeding": "Easy",
        "region": "North America",
        "holdings": {
            "North America": "2.2 - Tri-State Zoo & Aquarium",},
},
"Pond Slider": {
        "common": "Pond Slider",
        "scientific": "Trachemys scripta",
        "info": "One of the most well-known turtles in the world, the pond slider is infamous for its large size and easy obtainability, leading to a large number of dumped or released pets. They are native to the southern United States and northern Mexico, but have spread globally and become a highly problematic invasive species in many locales.",
        "type": "Reptile",
        "order": "Testudines",
        "family": "Emydidae",
        "genus": "Trachemys",
    "images": [
        {"label": "Red-eared slider (elegans)", "url": "https://i.imgur.com/9JIk6A0.jpeg"},
    ],
    "breeding": "Average",
        "region": "North America",
        "holdings": {
            "North America": "1.2 [elegans] - Tri-State Zoo & Aquarium",},

},
"Golden Trevally": {
        "common": "Golden Trevally",
        "scientific": "Gnathanodon speciosus",
        "info": "A medium-sized and well-known trevally species of the Indo-Pacific, the golden trevally is commonly displayed in public aquaria due to its size, color, and speed, and is sometimes found in the home aquarium trade, but requires extremely large tanks. As they grow older they become more grayish and less golden.",
        "type": "Fish",
        "order": "Carangiformes",
        "family": "Carangidae",
        "genus": "Gnathanodon",
        "image_url": "https://i.imgur.com/TDrypCW.jpeg",
        "breeding": "Impossible",
        "region": "Asia, Africa, Oceania, South America",
        "holdings": {
            "North America": "17 - Tri-State Zoo & Aquarium",},

},
"Red River Hog": {
        "common": "Red River Hog",
        "scientific": "Potamochoerus porcus",
        "info": "Rarely seen in the wild but common in captivity, the red river hog is a unique pig species native to tropical Africa, specifically west and central Africa. They are social animals, and breed very well in captivity, leading to a rapid expansion in their captive population in recent years.",
        "type": "Mammal",
        "order": "Artiodactyla",
        "family": "Suidae",
        "genus": "Potamochoerus",
        "image_url": "https://i.imgur.com/cgCItCX.png",
        "breeding": "Easy",
        "region": "Africa",
        "holdings": {
            "North America": "1.3 - Tri-State Zoo & Aquarium",},

},
"Lookdown": {
        "common": "Lookdown",
        "scientific": "Selene vomer",
        "info": "A small carangid native to the greater Caribbean ecosystem, the lookdown is known for its distinctive compressed appearance and large forehead. A schooling species, they are extremely sensitive in captivity and are regarded as a challening yet rewarding species to keep.",
        "type": "Fish",
        "order": "Carangiformes",
        "family": "Carangidae",
        "genus": "Selene",
        "image_url": "https://i.imgur.com/0vzqJP8.jpeg",
        "breeding": "Impossible",
        "region": "North America, South America",
        "holdings": {
            "North America": "14 - Tri-State Zoo & Aquarium",},

},
"Allen's Swamp Monkey": {
        "common": "Allen's Swamp Monkey",
        "scientific": "Allenopithecus nigroviridis",
        "info": "The Allen's swamp monkey is a distinctive type of guenon native to central Africa, in swampy, water-rich environments. They feed mainly on fruits, leaves, and insects. Little is known about the species in the wild, and most knowledge comes from captive specimens.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Cercopithecidae",
        "genus": "Allenopithecus",
        "image_url": "https://i.imgur.com/v3qLlO8.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "North America": "2.3 - Tri-State Zoo & Aquarium",},

},
"Jackson's Hornbill": {
        "common": "Jackson's Hornbill",
        "scientific": "Tockus jacksoni",
        "info": "The Jackson's hornbill is a small hornbill closely related to and often synonymized with the more well-known Von der Decken's hornbill. It is distinguished by the white spots on its wings, and typically feeds on insects and seeds.",
        "type": "Bird",
        "order": "Bucerotiformes",
        "family": "Bucerotidae",
        "genus": "Tockus",
        "image_url": "https://i.imgur.com/sH7sIUX.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "North America": "1.0 - Tri-State Zoo & Aquarium",},

},
"Violet-Backed Starling": {
        "common": "Violet-Backed Starling",
        "scientific": "Cinnyricinclus leucogaster",
        "info": "A smallish starling native to sub-Saharan Africa, the violet-backed starling has intense sexual dimorphism, with the males a bright purple, while the females are cryptically colored. It is an edge specialist that also inhabits woodlands, and is rarely seen on the ground.",
        "type": "Bird",
        "order": "Passeriformes",
        "family": "Sturnidae",
        "genus": "Cinnyricinclus",
        "image_url": "https://i.imgur.com/yDJNw3Q.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "North America": "2.2 - Tri-State Zoo & Aquarium",},

},
"Superb Starling": {
        "common": "Superb Starling",
        "scientific": "Lamprotornis superbus",
        "info": "Native to east Africa, the superb starling has distinctive, striking coloration. This is a hardy and long-lived species that lives for 15+ years in the correct conditions, and primarily rely on insects as their main source of food.",
        "type": "Bird",
        "order": "Passeriformes",
        "family": "Sturnidae",
        "genus": "Lamprotornis",
        "image_url": "https://i.imgur.com/2fhLet3.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "North America": "2.3 - Tri-State Zoo & Aquarium",},

},
"Golden-Breasted Starling": {
        "common": "Golden-Breasted Starling",
        "scientific": "Lamprotornis regius",
        "info": "The golden-breasted starling is found throughout northeastern Africa, and has a distinctive long pair of tail feathers that allows for easy identification. Feeding on insects and other small animals, they are an adaptable species, inhabiting savannahs, dry forests, and scrubland.",
        "type": "Bird",
        "order": "Passeriformes",
        "family": "Sturnidae",
        "genus": "Lamprotornis",
        "image_url": "https://i.imgur.com/ugZPTzy.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "North America": "2.2 - Tri-State Zoo & Aquarium",},

},
"Blue-Bellied Roller": {
        "common": "Blue-Bellied Roller",
        "scientific": "Coracias cyanogaster",
        "info": "The blue-bellied roller is a common and somewhat widespread species of roller that can be found in a thin range stretching from Senegal to South Sudan. They have good eyesight and are known to watch for large insects such as grasshoppers, swooping down to attack and eat them when they are seen.",
        "type": "Bird",
        "order": "Coraciiformes",
        "family": "Coraciidae",
        "genus": "Coracias",
        "image_url": "https://i.imgur.com/O0VXIaN.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "North America": "1.1 - Tri-State Zoo & Aquarium",},

},
"Rosy-Faced Lovebird": {
        "common": "Rosy-Faced Lovebird",
        "scientific": "Agapornis roseicollis",
        "info": "The rosy-faced lovebird is a common and well-studied species of lovebird that is often kept in aviculture and in zoological facilities. They have very variable coloration in captivity due to selective breeding, and have been studied as a model of bird genetics.",
        "type": "Bird",
        "order": "Psittaciformes",
        "family": "Psittaculidae",
        "genus": "Agapornis",
        "image_url": "https://i.imgur.com/7iqnKKD.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "North America": "6.6 - Tri-State Zoo & Aquarium",},

},
"Senegal Parrot": {
        "common": "Senegal Parrot",
        "scientific": "Poicephalus senegalus",
        "info": "A common species in aviculture, the Senegal parrot is native to west and central Africa, and is regarded as a pest there, but is popular amongst bird enthusiasts for its relative quietness and friendliness compared to other parrot species.",
        "type": "Bird",
        "order": "Psittaciformes",
        "family": "Psittacidae",
        "genus": "Poicephalus",
        "image_url": "https://i.imgur.com/4nNKoz4.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "North America": "4.4 - Tri-State Zoo & Aquarium",},

},
"African Pygmy Falcon": {
        "common": "African Pygmy Falcon",
        "scientific": "Polihierax semitorquatus",
        "info": "The African pygmy falcon is one of the smallest birds of prey on Earth, and the smallest in Africa. They feed primarily on reptiles and insects due to their small size, and are known for their interesting relationships with weaver birds, as they live in their nests and occasionally feed on them.",
        "type": "Bird",
        "order": "Falconiformes",
        "family": "Falconidae",
        "genus": "Polihierax",
        "image_url": "https://i.imgur.com/HZ0lmhD.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "North America": "1.1 - Tri-State Zoo & Aquarium",},

},
"Gray Crowned Crane": {
        "common": "Gray Crowned Crane",
        "scientific": "Balearica regulorum",
        "info": "One of the most well-known cranes, the gray crowned crane is native to sub-Saharan Africa, residing in savannah and wetlands habitats. They are endangered due to habitat loss and pesticide pollution, and is the national bird of Uganda, where it is held in great respect.",
        "type": "Bird",
        "order": "Gruiformes",
        "family": "Gruidae",
        "genus": "Balearica",
        "image_url": "https://i.imgur.com/yYP5Orb.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "North America": "1.2 - Tri-State Zoo & Aquarium",},

},
"African Pygmy Goose": {
        "common": "African Pygmy Goose",
        "scientific": "Nettapus auritus",
        "info": "The African pygmy goose is one of the world's smallest waterfowl and the smallest in Africa. Native to both the mainland of the continent and Madagascar, the species is regarded as somewhat difficult to breed in captivity, unlike most waterfowl.",
        "type": "Bird",
        "order": "Anseriformes",
        "family": "Anatidae",
        "genus": "Nettapus",
        "image_url": "https://i.imgur.com/MZ5xEWy.jpeg",
        "breeding": "Below Average",
        "region": "Africa",
        "holdings": {
            "Europe": "1.3 - Parque Zoologico de Clear Coast",},

},
"Great Blue Turaco": {
        "common": "Great Blue Turaco",
        "scientific": "Corythaeola cristata",
        "info": "The largest turaco species, the great blue turaco is a fairly typical member of its order in terms of behavior. With poor flying ability, they jump from branch to branch to find their preferred food, which is primarily leaves and flowers.",
        "type": "Bird",
        "order": "Musophagiformes",
        "family": "Musophagidae",
        "genus": "Corythaeola",
        "image_url": "https://i.imgur.com/k4JsoUu.jpeg",
        "breeding": "Difficult",
        "region": "Africa",
        "holdings": {
            "Europe": "2.2 - Parque Zoologico de Clear Coast",},

},
"Fennec Fox": {
        "common": "Fennec Fox",
        "scientific": "Vulpes zerda",
        "info": "The fennec fox is the smallest fox and smallest canid overall, only getting to 15 inches in length and 3 lbs in weight. Its large ears allow for increased hearing ability in the desert and heat dissipation, a necessity in its range.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Canidae",
        "genus": "Vulpes",
        "image_url": "https://i.imgur.com/XbwYPID.jpeg",
        "breeding": "Average",
        "region": "Asia, Africa",
        "holdings": {
            "Europe": "0.1.2.0 - Parque Zoologico de Clear Coast",},

},
"Crab-Eating Fox": {
        "common": "Crab-Eating Fox",
        "scientific": "Cerdocyon thous",
        "info": "Despite its common name, the crab-eating fox of South America is not closely related to true foxes, as are all fox-like canids in South America. As its name suggests, it feeds primarily on crabs and other invertebrates in wetland environments.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Canidae",
        "genus": "Cerdocyon",
        "image_url": "https://i.imgur.com/yDMQZ5j.jpeg",
        "breeding": "Below Average",
        "region": "South America",
        "holdings": {
            "Europe": "2.0 - Parque Zoologico de Clear Coast",},

},
"Bat-Eared Fox": {
        "common": "Bat-Eared Fox",
        "scientific": "Otocyon megalotis",
        "info": "Named for its large ears, which help both with thermoregulation and prey detection, the bat-eared fox is unique amongst canids as it has a primarily insectivorous diet. Native to sub-Saharan Africa, it can be found in two disjunct ranges in east and southern Africa.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Canidae",
        "genus": "Otocyon",
        "image_url": "https://i.imgur.com/jeIscAo.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "Europe": "1.1 - Parque Zoologico de Clear Coast",},

},
"Toco Toucan": {
        "common": "Toco Toucan",
        "scientific": "Ramphastos toco",
        "info": "The largest toucan and the most well-known, the toco toucan is primarily distributed in grasslands, wetlands, and secondary forest. They primarily feed on fruit but opportunistically take other food as well, such as insects and small birds.",
        "type": "Bird",
        "order": "Piciformes",
        "family": "Ramphastidae",
        "genus": "Ramphastos",
        "image_url": "https://i.imgur.com/u1pRDX2.jpeg",
        "breeding": "Difficult",
        "region": "South America",
        "holdings": {
            "Europe": "2.2 - Parque Zoologico de Clear Coast",},

},
"Green Jay": {
        "common": "Green Jay",
        "scientific": "Cyanocorax luxuosus",
        "info": "A brightly colored corvid found from Texas to Honduras, the green jay is variable in color, and in different parts of its range will have different plumage. Feeding primarily on insects and seeds, they will cache food for later when times are lean.",
        "type": "Bird",
        "order": "Passeriformes",
        "family": "Corvidae",
        "genus": "Cyanocorax",
        "image_url": "https://i.imgur.com/KMLyCHn.jpeg",
        "breeding": "Average",
        "region": "North America",
        "holdings": {
            "Europe": "2.2 - Parque Zoologico de Clear Coast",},

},
"African Jacana": {
        "common": "African Jacana",
        "scientific": "Actophilornis africanus",
        "info": "Widely distributed in African wetlands from coast to coast, the African jacana's distinctive large feet help it walk on sturdy water vegetation such as water lilies. They are probably the largest jacana species in weight.",
        "type": "Bird",
        "order": "Charadriiformes",
        "family": "Jacanidae",
        "genus": "Actophilornis",
        "image_url": "https://i.imgur.com/oxsjtXY.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "Europe": "1.3 - Parque Zoologico de Clear Coast",},

},
"Black-Backed Jackal": {
        "common": "Black-Backed Jackal",
        "scientific": "Lupulella mesomelas",
        "info": "A medium-sized canid native to sub-Saharan Africa, the black-backed jackal is a prominent species in its ecosystem. An omnivore, it will feed on a variety of food both alive and dead, but is regarded as a pest in some of its range due to its propensity for livestock predation.",
        "type": "Mammal",
        "order": "Carnivora",
        "family": "Canidae",
        "genus": "Lupulella",
        "image_url": "https://i.imgur.com/q6LZNnk.jpeg",
        "breeding": "Average",
        "region": "Africa",
        "holdings": {
            "Europe": "1.1 - Parque Zoologico de Clear Coast",},

},
"Yellow Oriole": {
        "common": "Yellow Oriole",
        "scientific": "Icterus nigrogularis",
        "info": "Native to northern South America along the coast, the yellow oriole prefers open habitats, such as scrubland and disturbed areas. Their nest is a hanging basket, typically yielding three eggs. They feed primarily on insects, nectar, and fruit.",
        "type": "Bird",
        "order": "Passeriformes",
        "family": "Icteridae",
        "genus": "Icterus",
        "image_url": "https://i.imgur.com/06J2DgH.jpeg",
        "breeding": "Average",
        "region": "South America",
        "holdings": {
            "Europe": "1.2 - Parque Zoologico de Clear Coast",},

},
"Boat-Billed Heron": {
        "common": "Boat-Billed Heron",
        "scientific": "Cochlearius cochlearius",
        "info": "A distinctive Neotropical heron, the boat-billed heron resides primarily in mangrove swamps along the coasts and rivers. A predatory species, their large bill allows them to capture preferred prey such as fish and shrimp.",
        "type": "Bird",
        "order": "Pelecaniformes",
        "family": "Ardeidae",
        "genus": "Cochlearius",
        "image_url": "https://i.imgur.com/7v8NeDq.jpeg",
        "breeding": "Below Average",
        "region": "North America, South America",
        "holdings": {
            "Europe": "1.2 - Parque Zoologico de Clear Coast",},

},
"Wattled Jacana": {
        "common": "Wattled Jacana",
        "scientific": "Jacana jacana",
        "info": "One of the more well-known jancanas, the wattled jacana is found in Central and South America, from Panama down to Argentina. With its large, wide feet, it is able to walk across sturdy wetland vegetation quite easily.",
        "type": "Bird",
        "order": "Charadriiformes",
        "family": "Jacanidae",
        "genus": "Jacana",
        "image_url": "https://i.imgur.com/kVsDJR6.jpeg",
        "breeding": "Average",
        "region": "North America, South America",
        "holdings": {
            "Europe": "1.1 - Parque Zoologico de Clear Coast",},

},
"Otago Skink": {
        "common": "Otago Skink",
        "scientific": "Oligosoma otagense",
        "info": "A large, endangered skink, the Otago skink is endemic to the South Island of New Zealand, specifically in the Otago region. Its precipitous decline has been caused by the introduction of non-native species like cats and weasels.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Scincidae",
        "genus": "Oligosoma",
        "image_url": "https://i.imgur.com/k3S5QsE.jpeg",
        "breeding": "Below Average",
        "region": "Oceania",
        "holdings": {
            "Oceania": "1.1 - Auckland Conservation Zoo and Aquarium",},

},
"Grand Skink": {
        "common": "Grand Skink",
        "scientific": "Oligosoma grande",
        "info": "A large, endangered skink endemic to two small areas of the South Island of New Zealand, the grand skink is at the center of a national conservation effort in the country. Feeding on insects and fruits, their wild population has decline to about 2,000-5,000 individuals.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Scincidae",
        "genus": "Oligosoma",
        "image_url": "https://i.imgur.com/VyHED1L.jpeg",
        "breeding": "Below Average",
        "region": "Oceania",
        "holdings": {
            "Oceania": "1.1 - Auckland Conservation Zoo and Aquarium",},

},
"Copper Skink": {
        "common": "Copper Skink",
        "scientific": "Oligosoma aeneum",
        "info": "The copper skink is New Zealand's smallest native skink species, growing to 3 inches at the absolute maximum. Despite most New Zealand skinks being endangered, the copper skink seems to have adapted better and is somewhat common in human-inhabited areas.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Scincidae",
        "genus": "Oligosoma",
        "image_url": "https://i.imgur.com/zwAFEVd.jpeg",
        "breeding": "Below Average",
        "region": "Oceania",
        "holdings": {
            "Oceania": "1.1 - Auckland Conservation Zoo and Aquarium",},

},
"South Island Takahē": {
        "common": "South Island Takahē",
        "scientific": "Porphyrio hochstetteri",
        "info": "A flightless and multicolored swamphen endemic to New Zealand, the South Island takahē is the only remaining takahē species, as its relative from the North Island was wiped out in modern times. It is known to use its wings to help it climb slopes in its natural habitat.",
        "type": "Bird",
        "order": "Gruiformes",
        "family": "Rallidae",
        "genus": "Porphyrio",
        "image_url": "http://i.imgur.com/bvdto7o.jpeg",
        "breeding": "Difficult",
        "region": "Oceania",
        "holdings": {
            "Oceania": "2.3 - Auckland Conservation Zoo and Aquarium",},

},
"Kea": {
        "common": "Kea",
        "scientific": "Nestor notabilis",
        "info": "A large parrot, the kea is the only species of its entire order to be found in alpine environments. Renowned for their playful, intelligent, and sometimes aggressive nature, keas are endangered in the wild due to persecution and poisoning.",
        "type": "Bird",
        "order": "Psittaciformes",
        "family": "Strigopidae",
        "genus": "Nestor",
        "image_url": "https://i.imgur.com/zTHyOcv.jpeg",
        "breeding": "Difficult",
        "region": "Oceania",
        "holdings": {
            "Oceania": "2.3 - Auckland Conservation Zoo and Aquarium",},

},
"New Zealand Fantail": {
        "common": "New Zealand Fantail",
        "scientific": "Rhipidura fuliginosa",
        "info": "The New Zealand fantail is endemic to New Zealand and nearby islands, and is the only fantail found there. An active, almost frenetic bird, the New Zealand fantail feeds primarily on insects and are not scared of people, living often in urban environments.",
        "type": "Bird",
        "order": "Passeriformes",
        "family": "Rhipiduridae",
        "genus": "Rhipidura",
        "image_url": "https://i.imgur.com/cV5TUIx.jpeg",
        "breeding": "Below Average",
        "region": "Oceania",
        "holdings": {
            "Oceania": "4.3 - Auckland Conservation Zoo and Aquarium",},

},
"Tūī": {
        "common": "Tūī",
        "scientific": "Prosthemadera novaeseelandiae",
        "info": "The only member of its genus, the tūī is a distinctive honeyeater known for the tufts on the neck of the male. Females are much more cryptically colored, which allows them to blend into the nest.",
        "type": "Bird",
        "order": "Passeriformes",
        "family": "Meliphagidae",
        "genus": "Prosthemadera",
        "image_url": "https://i.imgur.com/0Blsesv.jpeg",
        "breeding": "Difficult",
        "region": "Oceania",
        "holdings": {
            "Oceania": "2.3 - Auckland Conservation Zoo and Aquarium",},

},
"New Zealand Longfin Eel": {
        "common": "New Zealand Longfin Eel",
        "scientific": "Anguilla dieffenbachii",
        "info": "One of the largest true eels in the world, the New Zealand longfin eel is one of the few remaining large animals endemic to the islands. Endangered due to overfishing and incidental killings, it is an apex predator throughout its range, and the species attracts tourists who watch and feed them.",
        "type": "Fish",
        "order": "Anguilliformes",
        "family": "Anguillidae",
        "genus": "Anguilla",
        "image_url": "https://i.imgur.com/vUGisyA.jpeg",
        "breeding": "Impossible",
        "region": "Oceania",
        "holdings": {
            "Oceania": "3 - Auckland Conservation Zoo and Aquarium",},

},
"Shortfin Eel": {
        "common": "Shortfin Eel",
        "scientific": "Anguilla australis",
        "info": "This eel species is found in temperate and tropical freshwater environments throughout Oceania, including in Australia, New Zealand, and Fiji. They have been aquacultured for thousands of years but are regarded as difficult to breed in captivity.",
        "type": "Fish",
        "order": "Anguilliformes",
        "family": "Anguillidae",
        "genus": "Anguilla",
        "image_url": "https://i.imgur.com/3emunmi.jpeg",
        "breeding": "Impossible",
        "region": "Oceania",
        "holdings": {
            "Oceania": "4 - Auckland Conservation Zoo and Aquarium",},

},
"Giant Kōkopu": {
        "common": "Giant Kōkopu",
        "scientific": "Galaxias argenteus",
        "info": "The giant kōkopu is the largest of all the galaxias, growing up to almost 2 feet long and 6 lbs in weight. A nocturnal predator, they feed on a range of freshwater invertebrates and small fish.",
        "type": "Fish",
        "order": "Galaxiiformes",
        "family": "Galaxiidae",
        "genus": "Galaxias",
        "image_url": "https://i.imgur.com/Mv4Twfd.jpeg",
        "breeding": "Impossible",
        "region": "Oceania",
        "holdings": {
            "Oceania": "4 - Auckland Conservation Zoo and Aquarium",},

},
"Redfin Bully": {
        "common": "Redfin Bully",
        "scientific": "Gobiomorphus huttoni",
        "info": "A generally cryptic amphidromous goby, the redfin bully is endemic to New Zealand. Males can be distinguished from females due to their bright red fins and distinctive brown stripes. Males and females have different prey preferences, a behavior rarely seen in fish.",
        "type": "Fish",
        "order": "Gobiiformes",
        "family": "Eleotridae",
        "genus": "Gobiomorphus",
        "image_url": "https://i.imgur.com/yaGmQLQ.jpeg",
        "breeding": "Impossible",
        "region": "Oceania",
        "holdings": {
            "Oceania": "4 - Auckland Conservation Zoo and Aquarium",},

},
"Giant Bully": {
        "common": "Giant Bully",
        "scientific": "Gobiomorphus gobioides",
        "info": "The largest species in its genus, the giant bully is endemic to New Zealand and can grow to 10 inches in length. Residing in rivers and estuaries, it is believed that the larvae need saltwater to develop properly.",
        "type": "Fish",
        "order": "Gobiiformes",
        "family": "Eleotridae",
        "genus": "Gobiomorphus",
        "image_url": "https://i.imgur.com/ehSb3i4.jpeg",
        "breeding": "Impossible",
        "region": "Oceania",
        "holdings": {
            "Oceania": "4 - Auckland Conservation Zoo and Aquarium",},

},
"Eldon's Galaxias": {
        "common": "Eldon's Galaxias",
        "scientific": "Galaxias eldoni",
        "info": "A endangered microendemic galaxias, the Eldon's galaxias is found only in the eastern part of Otago on the South Island of New Zealand. They are endangered due to introduced trout preying on them.",
        "type": "Fish",
        "order": "Galaxiiformes",
        "family": "Galaxiidae",
        "genus": "Galaxias",
        "image_url": "https://i.imgur.com/unAkUJ6.jpeg",
        "breeding": "Impossible",
        "region": "Oceania",
        "holdings": {
            "Oceania": "5 - Auckland Conservation Zoo and Aquarium",},

},
"Lowland Longjaw Galaxias": {
        "common": "Lowland Longjaw Galaxias",
        "scientific": "Galaxias cobitinis",
        "info": "The lowland longjaw galaxias is a highly endangered galaxias found only in the Kauru River on the South Island of New Zealand. Like all galaxias they lack scales and have a thick, mucus-based slime coat.",
        "type": "Fish",
        "order": "Galaxiiformes",
        "family": "Galaxiidae",
        "genus": "Galaxias",
        "image_url": "https://i.imgur.com/8j0PRTf.jpeg",
        "breeding": "Impossible",
        "region": "Oceania",
        "holdings": {
            "Oceania": "5 - Auckland Conservation Zoo and Aquarium",},

},
"Black Goby": {
        "common": "Black Goby",
        "scientific": "Gobius niger",
        "info": "Native to the northeastern Atlantic and the Mediterranean Sea, the black goby is a large species of goby found commonly in shallow areas such as tidepools. They have been known to occasionally enter freshwater, but not for long periods of time.",
        "type": "Fish",
        "order": "Gobiiformes",
        "family": "Gobiidae",
        "genus": "Gobius",
        "image_url": "https://i.imgur.com/vA0MEu3.jpeg",
        "breeding": "Impossible",
        "region": "Europe, Asia, Africa",
        "holdings": {
            "Europe": "2 - Blue Seas Aquarium",},

},
"Broadnosed Pipefish": {
        "common": "Broadnosed Pipefish",
        "scientific": "Syngnathus typhle",
        "info": "The broadnosed pipefish has a large, thick snout which is as thick as its body, lending it is common name. It is a temperate species native to the eastern Atlantic from Norway to Morocco. It is found at shallow depths no deeper than 66ft.",
        "type": "Fish",
        "order": "Syngnathiformes",
        "family": "Syngnathidae",
        "genus": "Syngnathus",
        "image_url": "https://i.imgur.com/nLcMLNn.jpeg",
        "breeding": "Difficult",
        "region": "Europe, Asia, Africa",
        "holdings": {
            "Europe": "6 - Blue Seas Aquarium",},

},
"Common Hermit Crab": {
        "common": "Common Hermit Crab",
        "scientific": "Pagurus bernhardus",
        "info": "As the name implies, the common hermit crab is the most abundant hermit crab in the northeast Atlantic. An omnivorous detritivore, it is unique amongst hermit crabs in the sense that it can also filter feed to gain food when necessary.",
        "type": "Invertebrate",
        "order": "Decapoda",
        "family": "Paguridae",
        "genus": "Pagurus",
        "image_url": "https://i.imgur.com/aFektJA.jpeg",
        "breeding": "Impossible",
        "region": "Europe",
        "holdings": {
            "Europe": "8 - Blue Seas Aquarium",},

},
"European Green Crab": {
        "common": "European Green Crab",
        "scientific": "Carcinus maenas",
        "info": "Originally native to the northeast Atlantic and the Baltic Sea, the European green crab is now an infamous invasive species globally. They reside in both marine and estuarine habitats and are highly adaptable, able to feed on a variety of invertebrates.",
        "type": "Invertebrate",
        "order": "Decapoda",
        "family": "Carcinidae",
        "genus": "Carcinus",
        "image_url": "https://i.imgur.com/MeZFzwk.jpeg",
        "breeding": "Impossible",
        "region": "Europe",
        "holdings": {
            "Europe": "3 - Blue Seas Aquarium",},

},
"Two-Spotted Goby": {
        "common": "Two-Spotted Goby",
        "scientific": "Pomatoschistus flavescens",
        "info": "A small goby reaching 2.4 inches in length, the two-spotted goby is a common sight in the North Atlantic and Mediterranean Sea. Unlike most gobies it inhabits heavily vegetated areas filled with seagrass and seaweed.",
        "type": "Fish",
        "order": "Gobiiformes",
        "family": "Oxudercidae",
        "genus": "Pomatoschistus",
        "image_url": "https://i.imgur.com/zTAEhC8.jpeg",
        "breeding": "Impossible",
        "region": "Europe",
        "holdings": {
            "Europe": "14 - Blue Seas Aquarium",},

},
"Lion's Mane Jellyfish": {
        "common": "Lion's Mane Jellyfish",
        "scientific": "Cyanea capillata",
        "info": "The lion's mane jellyfish is the largest species of jellyfish in the world. Exceptional specimens can grow to 7 feet in bell diameter and over 120 feet in length, but most specimens are much smaller. Regardless, this jellyfish species is large and bulky on average, and has few natural predators.",
        "type": "Invertebrate",
        "order": "Semaeostomeae",
        "family": "Cyaneidae",
        "genus": "Cyanea",
        "image_url": "https://i.imgur.com/zTAEhC8.jpeg",
        "breeding": "Impossible",
        "region": "North America, Europe, Asia",
        "holdings": {
            "Europe": "2 - Blue Seas Aquarium",},

},
"Common Blenny": {
        "common": "Common Blenny",
        "scientific": "Lipophrys pholis",
        "info": "Also known as the shanny or smooth blenny, the common blenny occurs in rocky areas of the northeastern Atlantic, and typically stay to one area their whole lives, except to find mates. They feed primarily on snails, barnacles, and amphipods.",
        "type": "Fish",
        "order": "Blenniiformes",
        "family": "Blenniidae",
        "genus": "Lipophrys",
        "image_url": "https://i.imgur.com/X9ZX0cn.jpeg",
        "breeding": "Impossible",
        "region": "Europe, Africa",
        "holdings": {
            "Europe": "4 - Blue Seas Aquarium",},

},
"Tompot Blenny": {
        "common": "Tompot Blenny",
        "scientific": "Parablennius gattorugine",
        "info": "A somewhat large blenny, the tompot blenny is native to the northeastern Atlantic and Mediterranean Sea. Utilizing their comb-like teeth, they scrape their food from the subtrate surrounding them in their tide pool habitats. Like many blennies, they rarely leave their home territory once established.",
        "type": "Fish",
        "order": "Blenniiformes",
        "family": "Blenniidae",
        "genus": "Parablennius",
        "image_url": "https://i.imgur.com/8ySC7fR.jpeg",
        "breeding": "Impossible",
        "region": "Europe, Asia, Africa",
        "holdings": {
            "Europe": "4 - Blue Seas Aquarium",},

},
"Beadlet Anemone": {
        "common": "Beadlet Anemone",
        "scientific": "Actinia equina",
        "info": "The beadlet anemone is a very widespread species of anemone found in cold water environments on multiple continents. They are somewhat variable in coloration, most commonly being a bright red. They are a long-lived species, with one individual (named Granny) living for 66 years.",
        "type": "Invertebrate",
        "order": "Actiniaria",
        "family": "Actiniidae",
        "genus": "Actinia",
        "image_url": "https://i.imgur.com/wZlhpt6.jpeg",
        "breeding": "Impossible",
        "region": "Europe, Asia, Africa",
        "holdings": {
            "Europe": "23 - Blue Seas Aquarium",},

},
"Strawberry Anemone": {
        "common": "Strawberry Anemone",
        "scientific": "Actinia fragacea",
        "info": "A shallow-water anemone native to the northeastern Atlantic, the strawberry anemone gets its common name from the red and green coloration of its foot. Little is known about its reproduction, but it is oviparous and has separate sexes.",
        "type": "Invertebrate",
        "order": "Actiniaria",
        "family": "Actiniidae",
        "genus": "Actinia",
        "image_url": "https://i.imgur.com/hmtRL0p.jpeg",
        "breeding": "Impossible",
        "region": "Europe, Africa",
        "holdings": {
            "Europe": "5 - Blue Seas Aquarium",},

},
"Clonal Plumose Anemone": {
        "common": "Clonal Plumose Anemone",
        "scientific": "Metridium senile",
        "info": "The clonal plumose anemone is a common North Atlantic anemone that has been described as 'the most beautiful anemone in the world'. They grow incredibly quickly, increasing to 2 inches in diameter in just 5 months, an unprecedented growth rate for cnidarians.",
        "type": "Invertebrate",
        "order": "Actiniaria",
        "family": "Metridiidae",
        "genus": "Metridium",
        "image_url": "https://i.imgur.com/RnSfKP4.jpeg",
        "breeding": "Impossible",
        "region": "North America, South America, Europe, Asia, Oceania",
        "holdings": {
            "Europe": "25 - Blue Seas Aquarium",},

},
"Common Cuttlefish": {
        "common": "Common Cuttlefish",
        "scientific": "Sepia officinalis",
        "info": "One of the largest and best-known cuttlefish, the common cuttlefish is a temperate-water species native to the northeastern Atlantic. With their color-changing chromatophores, they are known to change color incredibly quickly for various purposes, such as communication and camoflauge.",
        "type": "Invertebrate",
        "order": "Sepiida",
        "family": "Sepiidae",
        "genus": "Sepia",
        "image_url": "https://i.imgur.com/RANo632.jpeg",
        "breeding": "Difficult",
        "region": "Europe, Asia, Africa",
        "holdings": {
            "Europe": "4 - Blue Seas Aquarium",},

},
"Compass Jellyfish": {
        "common": "Compass Jellyfish",
        "scientific": "Chrysaora hysoscella",
        "info": "A common North Atlantic jellyfish, the compass jellyfish is a carnivorous species that feeds on a variety of pelagic invertebrates. They utilize both sexual and asexual reproduction, choosing the reproductive strategy that works best for them in the moment.",
        "type": "Invertebrate",
        "order": "Semaeostomeae",
        "family": "Pelagiidae",
        "genus": "Chrysaora",
        "image_url": "https://i.imgur.com/7LaLac3.jpeg",
        "breeding": "Difficult",
        "region": "Europe, Africa",
        "holdings": {
            "Europe": "5 - Blue Seas Aquarium",},

},
"Amur Carp": {
        "common": "Amur Carp",
        "scientific": "Cyprinus rubrofuscus",
        "info": "The wild ancestor of koi carp, the Amur carp originates in north, east, and southeast Asia and is a fairly rare and uncommonly encountered species in its native range. Outside of their native range they have been introduced to various areas, either intentionally or accidentally, and have become a prized sport fish.",
        "type": "Fish",
        "order": "Cypriniformes",
        "family": "Cyprinidae",
        "genus": "Cyprinus",
        "image_url": "https://i.imgur.com/CrW6WF9.jpeg",
        "breeding": "Difficult",
        "region": "Asia",
        "holdings": {
            "North America": "6 - Chicago City Aquarium",},

},
"Black Crappie": {
        "common": "Black Crappie",
        "scientific": "Pomoxis nigromaculatus",
        "info": "The black crappie is a distinctive centrarchid whose original distribution is somewhat unclear. It is believed to originally come from eastern North America, but has been transplanted to many locations for sport fishing purposes. They are a hardy, adaptable species that can be found in a variety of habitats.",
        "type": "Fish",
        "order": "Centrarchiformes",
        "family": "Centrarchidae",
        "genus": "Pomoxis",
        "image_url": "https://i.imgur.com/bxq0GGs.jpeg",
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {
            "North America": "5 - Chicago City Aquarium",},

},
"Clown Loach": {
        "common": "Clown Loach",
        "scientific": "Chromobotia macracanthus",
        "info": "The clown loach is a large botia native to southeast Asia, specifically Indonesia on the islands of Sumatra and Borneo. They are one of the most popular botiid loaches in the aquarium trade, prized for their mellow demeanor, large size, and bright color.",
        "type": "Fish",
        "order": "Cypriniformes",
        "family": "Botiidae",
        "genus": "Chromobotia",
        "image_url": "https://i.imgur.com/XEawRQ8.jpeg",
        "breeding": "Impossible",
        "region": "Asia",
        "holdings": {
            "North America": "8 - Chicago City Aquarium",},

},
"Golden Redhorse": {
        "common": "Golden Redhorse",
        "scientific": "Moxostoma erythrurum",
        "info": "The golden redhorse is found in the central and eastern regions of North America in the United States and Canada. They are a bottom-feeding carnivore, feeding on small benthic animals such as snails and crustaceans. A popular game fish, they are commonly stocked for this purpose.",
        "type": "Fish",
        "order": "Cypriniformes",
        "family": "Catostomidae",
        "genus": "Moxostoma",
        "image_url": "https://i.imgur.com/S4Vd1uK.jpeg",
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {
            "North America": "3 - Chicago City Aquarium",},

},
"Quillback Carpsucker": {
        "common": "Quillback Carpsucker",
        "scientific": "Carpiodes cyprinus",
        "info": "Also known simply as the quillback, this distinctive catostomid found throughout North America. They are long-lived fish, typically living 30-50 years, and are sometimes utilized as game fish.",
        "type": "Fish",
        "order": "Cypriniformes",
        "family": "Catostomidae",
        "genus": "Carpiodes",
        "image_url": "https://i.imgur.com/viPGmB7.png",
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {
            "North America": "3 - Chicago City Aquarium",},

},
"Northern Hogsucker": {
        "common": "Northern Hogsucker",
        "scientific": "Hypentelium nigricans",
        "info": "The northern hogsucker is a medium-sized catostomid that can be found in clear, fast-flowing freshwater environments in the eastern and southern parts of North America. They are an indicator species, as they do not tolerate adverse water conditions readily.",
        "type": "Fish",
        "order": "Cypriniformes",
        "family": "Catostomidae",
        "genus": "Hypentelium",
        "image_url": "https://i.imgur.com/GNQTp4c.jpeg",
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {
            "North America": "5 - Chicago City Aquarium",},

},
"Northern Snakehead": {
        "common": "Northern Snakehead",
        "scientific": "Channa argus",
        "info": "The northern snakehead is a temperate species of snakehead native to north and east Asia, from Russia to China. A facultative air-breather, they are able to use a primitive labyrinth organ to intake air in addition to their gills in order to intake supplementary oxygen in poor water conditions.",
        "type": "Fish",
        "order": "Anabantiformes",
        "family": "Channidae",
        "genus": "Channa",
        "image_url": "https://i.imgur.com/bkSgabZ.jpeg",
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {
            "North America": "2 - Chicago City Aquarium",},

},
"Rainbow Darter": {
        "common": "Rainbow Darter",
        "scientific": "Etheostoma caeruleum",
        "info": "One of the most well-known darters, the rainbow darter can be found in central, southern, and eastern North America in the United States and Canada. Typical of darters, they live in fast-flowing, cool, clean rivers.",
        "type": "Fish",
        "order": "Perciformes",
        "family": "Percidae",
        "genus": "Etheostoma",
        "image_url": "https://i.imgur.com/FxAlyNU.jpeg",
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {
            "North America": "6 - Chicago City Aquarium",},

},
"Round Goby": {
        "common": "Round Goby",
        "scientific": "Neogobius melanostomus",
        "info": "A common goby in its native Eurasian range, the round goby has been introduced accidentally to the Great Lakes through ballast water discharge. They are an incredibly destructive species there, and measures are being taken to control the species.",
        "type": "Fish",
        "order": "Gobiiformes",
        "family": "Gobiidae",
        "genus": "Neogobius",
        "image_url": "https://i.imgur.com/m9uSsU1.jpeg",
        "breeding": "Impossible",
        "region": "Europe, Asia",
        "holdings": {
            "North America": "4 - Chicago City Aquarium",},

},
"Yellow Perch": {
        "common": "Yellow Perch",
        "scientific": "Perca flavescens",
        "info": "The yellow perch is one of the most common, widespread, and well-known members of its family. A popular gamefish, the yellow perch is a mesopredatory species that feeds primarily on invertebrates and small fish. They are commonly stocked for sport fishing purposes.",
        "type": "Fish",
        "order": "Perciformes",
        "family": "Percidae",
        "genus": "Perca",
        "image_url": "https://i.imgur.com/svpG3dV.jpeg",
        "breeding": "Impossible",
        "region": "North America",
        "holdings": {
            "North America": "6 - Chicago City Aquarium",},

},
"Black-and-White Ruffed Lemur": {
        "common": "Black-and-White Ruffed Lemur",
        "scientific": "Varecia variegata",
        "info": "The black-and-white ruffed lemur is a critically endangered lemur native to the tropical rainforests of Madagascar. They are frugivorous, with fruit making up 92% of their diet. They have a complex social structure and unique reproductive biology, such as large litters and short gestation periods.",
        "type": "Mammal",
        "order": "Primates",
        "family": "Lemuridae",
        "genus": "Varecia",
        "image_url": "https://i.imgur.com/3jAoTFw.jpeg",
        "breeding": "Above Average",
        "region": "Africa",
        "holdings": {
            "North America": "1.1 - Las Vegas Zoo",},

                                },
}

SPECIES = species_data

# -------- Canonicalize existing zoo names (APPLIES TO OLD DATA) --------
_ZOO_SPACE_NORM = re.compile(r"\s+")

def _norm_zoo(s : str) -> str:
    return _ZOO_SPACE_NORM.sub(" ", (s or "").strip().lower())

def _title_zoo(s: str) -> str:
    return " ".join(part.capitalize() for part in _ZOO_SPACE_NORM.sub(" ", (s or "").strip()).split())

def _canonical_from_directory(data: dict, raw: str) -> str:
    """
    Prefer exact directory casing via your resolver; else match directory keys by normalized form;
    else fall back to Title Case so old entries still look nice.
    """
    try:
        exact, suggestion = resolve_institution_name(raw.strip())
        if exact:
            return exact
    except Exception:
        pass

    directory = (data.get("directory") or {})
    nz = _norm_zoo(raw)
    for dn in directory.keys():
        if _norm_zoo(dn) == nz:
            return dn
    return _title_zoo(raw)

def _migrate_canonicalize_zoos(data: dict) -> bool:
    """
    - Rewrites ownership lists to canonical names (keeps limit, order doesn’t matter)
    - Re-keys users[uid]['zoos'] buckets to canonical names and merges dup buckets
    - Dedupes & canonicalizes species names per bucket
    Returns True if data changed.
    """
    changed = False

    # Ownership list
    ownership = data.get("ownership") or {}
    for uid, rec in ownership.items():
        zoos = list(rec.get("zoos") or [])
        new_list, seen = [], set()
        for z in zoos:
            cz = _canonical_from_directory(data, z)
            if cz not in seen:
                new_list.append(cz); seen.add(cz)
            if cz != z:
                changed = True
        rec["zoos"] = new_list

    # User zoo buckets
    users = data.get("users") or {}
    for uid, urec in users.items():
        zmap = dict(urec.get("zoos") or {})
        if not isinstance(zmap, dict):
            continue
        newmap: dict[str, list[str]] = {}
        for zname, species in zmap.items():
            cz = _canonical_from_directory(data, zname)
            lst = list(species or [])
            # merge duplicate buckets after canonicalization
            bucket = newmap.setdefault(cz, [])
            bucket.extend(lst)
            if cz != zname:
                changed = True

        # Deduplicate & canonicalize species entries per bucket
        for cz, lst in newmap.items():
            out, seen = [], set()
            for s in lst:
                if not isinstance(s, str):
                    continue
                cs = _canonical_species_name(s) or s.strip()
                if cs and cs not in seen:
                    seen.add(cs); out.append(cs)
            newmap[cz] = out

        urec["zoos"] = newmap

    return changed

# somewhere near your other config helpers
_PROGRESS_CFG_PATH = "progress_roles.json"

def _load_progress_cfg() -> dict:
    try:
        with open(_PROGRESS_CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_progress_cfg(cfg: dict) -> None:
    with open(_PROGRESS_CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# Normalizer used elsewhere in your bot
_ZOO_NORMALIZER = re.compile(r"\s+")

def _norm_zoo(name: str) -> str:
    return _ZOO_NORMALIZER.sub(" ", (name or "").strip()).lower()

def _load_zoo_data() -> dict:
    # you already have this in your codebase; this is just here to show calls
    ...

def _get_directory_species_for_zoo(zoo_name: str, data: dict) -> list[str]:
    """Prefer canonical directory collection; fallback to owner's collections."""
    directory = data.get("directory", {}) or {}
    for k, v in directory.items():
        if isinstance(k, str) and _norm_zoo(k) == _norm_zoo(zoo_name):
            species = (v or {}).get("species")
            if isinstance(species, list):
                return [s for s in species if isinstance(s, str)]
    # fallback: find an owner who has a 'collections' list for this zoo
    for _uid, urec in (data.get("users", {}) or {}).items():
        coll = (urec.get("collections", {}) or {}).get(zoo_name)
        if isinstance(coll, list) and coll:
            return [s for s in coll if isinstance(s, str)]
    return []

def get_display_image_url(url: str) -> str | None:
    if not url:
        return None

    url = url.strip()

    # Already a Wikimedia thumb URL
    if "upload.wikimedia.org" in url and "/thumb/" in url:
        return url

    # Convert original Wikimedia file URL -> thumb URL
    if "upload.wikimedia.org/wikipedia/commons/" in url and "/thumb/" not in url:
        parts = url.split("/commons/", 1)
        if len(parts) == 2:
            path = parts[1]
            filename = path.split("/")[-1]
            return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{path}/1200px-{filename}"

    if "commons.wikimedia.org/wiki/File:" in url:
        return None

    if "wikipedia.org/wiki/" in url:
        return None

    return url

def _get_owner_housed_for_zoo(zoo_name: str, data: dict) -> tuple[int|None, list[str]]:
    """
    Return (owner_uid, housed_species) for the zoo.
    If multiple users have entries, pick the one with the largest housed list.
    """
    best_uid, best_list = None, []
    for uid, urec in (data.get("users", {}) or {}).items():
        zoos = (urec.get("zoos", {}) or {})
        for zname, housed_list in zoos.items():
            if isinstance(zname, str) and _norm_zoo(zname) == _norm_zoo(zoo_name):
                cur = [s for s in (housed_list or []) if isinstance(s, str)]
                if len(cur) > len(best_list):
                    best_uid, best_list = int(uid), cur
    return best_uid, best_list

def housed_ratio_for_zoo(zoo_name: str) -> tuple[float, int, int, int|None]:
    """
    Returns (ratio_0to1, housed_count, held_count, owner_uid).
    Ratio is 0 if held_count == 0.
    """
    data = _load_zoo_data()
    owner_uid, housed = _get_owner_housed_for_zoo(zoo_name, data)
    held = _get_directory_species_for_zoo(zoo_name, data)
    if not isinstance(held, list):
        held = []
    # Only count housed if it’s actually part of the zoo’s held collection
    held_set = {s.lower().strip() for s in held}
    housed_valid = [s for s in housed if isinstance(s, str) and s.lower().strip() in held_set]
    held_n = len(held_set)
    housed_n = len(housed_valid)
    ratio = (housed_n / held_n) if held_n > 0 else 0.0
    return ratio, housed_n, held_n, owner_uid

def any_zoo_over_50_for_user(uid: int) -> bool:
    """True if this user owns any zoo that’s ≥ 50% housed."""
    data = _load_zoo_data()
    urec = (data.get("users", {}) or {}).get(str(uid)) or {}
    zoos = (urec.get("zoos", {}) or {})
    for zname in zoos.keys():
        ratio, _, _, owner_uid = housed_ratio_for_zoo(zname)
        if owner_uid == uid and ratio >= 0.5:
            return True
    return False

from discord.ext import tasks

PROGRESS_CMD_PERMS = commands.has_permissions(manage_roles=True)

def _get_progress_role(guild: discord.Guild) -> discord.Role | None:
    cfg = _load_progress_cfg()
    role_id = (cfg.get(str(guild.id)) or {}).get("role_id")
    if role_id:
        return guild.get_role(int(role_id))
    return None

async def _set_progress_role(guild: discord.Guild, role: discord.Role):
    cfg = _load_progress_cfg()
    cfg[str(guild.id)] = {"role_id": int(role.id)}
    _save_progress_cfg(cfg)

async def recompute_progress_role_for_guild(guild: discord.Guild):
    """
    Give the configured role to members who have ANY zoo at ≥50% housed (as owner).
    Remove it from members who no longer qualify.
    """
    role = _get_progress_role(guild)
    if not role:
        return

    # Build a set of members who should have the role
    should_have: set[int] = set()
    for member in guild.members:
        if member.bot:
            continue
        try:
            if any_zoo_over_50_for_user(member.id):
                should_have.add(member.id)
        except Exception:
            # don't break the whole run on a bad record
            continue

    # Apply changes (batched to reduce churn)
    # Add role
    for member in guild.members:
        if member.id in should_have and role not in member.roles:
            try:
                await member.add_roles(role, reason="≥50% housed (auto)")
            except discord.Forbidden:
                pass

    # Remove role
    for member in guild.members:
        if member.id not in should_have and role in member.roles:
            try:
                await member.remove_roles(role, reason="<50% housed (auto)")
            except discord.Forbidden:
                pass

@tasks.loop(minutes=10)
async def progress_role_sweeper():
    # Periodically reconcile across all guilds the bot is in
    for guild in bot.guilds:
        try:
            await recompute_progress_role_for_guild(guild)
        except Exception:
            log.exception("Progress role sweep failed for guild %s", guild.id)

@progress_role_sweeper.before_loop
async def _wait_until_ready():
    await bot.wait_until_ready()



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

# ---------- Helpers for view pagination (NEW) ----------
PAGE_SIZE = 20  # items per page

def _user_housed_list_for_zoo(data: dict, user_id: int, zoo_name: str) -> list[str]:
    """Return the current user's valid housed species for the given zoo (canonicalized + deduped + sorted)."""
    urec = (data.get("users", {}) or {}).get(str(user_id), {}) or {}
    housed = list(urec.get("zoos", {}).get(zoo_name, []) or [])
    housed_valid = []
    seen = set()
    for s in housed:
        canon = _canonical_species_name(s)
        if canon and canon not in seen:
            housed_valid.append(canon); seen.add(canon)
    housed_valid.sort(key=str.lower)
    return housed_valid

def _unhoused_list_for_zoo(data: dict, user_id: int, zoo_name: str) -> list[str]:
    """Return the catalog species for this zoo that the user has NOT housed yet (sorted)."""
    catalog = set(_catalog_species_for_zoo(zoo_name) or [])
    housed = set(_user_housed_list_for_zoo(data, user_id, zoo_name))
    missing = sorted([s for s in catalog if s not in housed], key=str.lower)
    return missing

def _slice_page(items: list[str], page: int, per_page: int = PAGE_SIZE) -> tuple[list[str], int]:
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return items[start:start + per_page], total_pages

def _build_zoo_progress_embed(ctx, zoo_name: str, data: dict, user_id: int,
                              category: str, page: int, per_page: int = PAGE_SIZE) -> discord.Embed:
    """category: 'housed' or 'unhoused'."""
    assert category in ("housed", "unhoused")
    housed = _user_housed_list_for_zoo(data, user_id, zoo_name)
    catalog_set = set(_catalog_species_for_zoo(zoo_name) or [])
    denom = len(catalog_set)
    num = len([s for s in housed if s in catalog_set])
    pct = _percent(num, denom)

    items = housed if category == "housed" else _unhoused_list_for_zoo(data, user_id, zoo_name)
    page_items, total_pages = _slice_page(items, page, per_page)

    # Title + summary bar
    bar_len = 20
    filled = round((pct / 100) * bar_len) if denom else 0
    bar = "█" * filled + "—" * (bar_len - filled)

    title = f"{zoo_name} — {num}/{denom} housed ({pct:.1f}%)"
    desc = f"`{bar}`\n**Viewing:** {('Housed' if category == 'housed' else 'Unhoused')} species"

    e = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
    # Try to carry forward any zoo image/location if you store them
    meta = (data.get("directory", {}) or {}).get(zoo_name, {}) or {}
    if isinstance(meta.get("image"), str) and meta["image"].strip():
        e.set_thumbnail(url=meta["image"].strip())
    elif isinstance(meta.get("image_url"), str) and meta["image_url"].strip():
        e.set_thumbnail(url=meta["image_url"].strip())
    if isinstance(meta.get("location"), str) and meta["location"].strip():
        e.add_field(name="Location", value=meta["location"].strip(), inline=False)

    # Page body
    if page_items:
        lines = [f"• {s}" for s in page_items]
        e.add_field(
            name=f"{'Housed' if category == 'housed' else 'Unhoused'} (showing {len(page_items)} of {len(items)})",
            value="\n".join(lines),
            inline=False
        )
    else:
        e.add_field(
            name=f"{'Housed' if category == 'housed' else 'Unhoused'}",
            value="_None_",
            inline=False
        )

    e.set_footer(text=f"Page {page + 1}/{total_pages} • Use ◀️ ▶️  • Toggle with 🔁")
    return e

# ---------- Paginated view for ;zoo view (NEW) ----------
class ZooViewPager(discord.ui.View):
    def __init__(self, ctx: commands.Context, zoo_name: str, data: dict,
                 start_category: str = "housed", start_page: int = 0):
        super().__init__(timeout=120)  # 2 min to prevent zombie views
        self.ctx = ctx
        self.zoo_name = zoo_name
        self.data = data
        self.category = start_category  # 'housed' | 'unhoused'
        self.page = start_page

    # Prev
    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command invoker can use these controls.", ephemeral=True)
            return
        # compute total pages to wrap-around
        items = (_user_housed_list_for_zoo if self.category == "housed" else _unhoused_list_for_zoo)(
            self.data, self.ctx.author.id, self.zoo_name
        )
        _, total_pages = _slice_page(items, 0)  # just to get count
        self.page = (self.page - 1) % total_pages
        e = _build_zoo_progress_embed(self.ctx, self.zoo_name, self.data, self.ctx.author.id, self.category, self.page)
        await interaction.response.edit_message(embed=e, view=self)

    # Toggle housed/unhoused
    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.primary)
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command invoker can use these controls.", ephemeral=True)
            return
        self.category = "unhoused" if self.category == "housed" else "housed"
        self.page = 0
        e = _build_zoo_progress_embed(self.ctx, self.zoo_name, self.data, self.ctx.author.id, self.category, self.page)
        await interaction.response.edit_message(embed=e, view=self)

    # Next
    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the command invoker can use these controls.", ephemeral=True)
            return
        items = (_user_housed_list_for_zoo if self.category == "housed" else _unhoused_list_for_zoo)(
            self.data, self.ctx.author.id, self.zoo_name
        )
        _, total_pages = _slice_page(items, 0)
        self.page = (self.page + 1) % total_pages
        e = _build_zoo_progress_embed(self.ctx, self.zoo_name, self.data, self.ctx.author.id, self.category, self.page)
        await interaction.response.edit_message(embed=e, view=self)


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

async def _send_list_or_file(ctx, title: str, lines: list[str], filename: str, inline_limit: int = 100):
    """
    If `lines` is longer than `inline_limit`, send as a .txt file.
    Otherwise, send as a normal message.
    """
    if not lines:
        await ctx.send(f"{title}\n(none)")
        return

    if len(lines) <= inline_limit:
        await ctx.send(f"{title}\n" + "\n".join(lines))
        return

    # Build .txt file for large outputs
    import io, discord  # assumes discord.py already imported
    content = title + "\n\n" + "\n".join(line.lstrip("• ").strip() for line in lines)
    buf = io.BytesIO(content.encode("utf-8"))
    buf.seek(0)
    file = discord.File(buf, filename=filename)
    await ctx.send(f"{title} (exported to file below):", file=file)

import re
from difflib import get_close_matches

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

def _norm_spaces(s: str) -> str:
    return " ".join(str(s).casefold().replace("–", "-").replace("—", "-").split())

def _key_name(s: str) -> str:
    return _NON_ALNUM.sub("", str(s).casefold())

def _resolve_zoo_canonical(input_name: str, data: dict) -> tuple[str, set[str]]:
    """
    Return (canonical_zoo_name, alias_norms) using directory keys/aliases.
    - Exact normalized match beats fuzzy match.
    - If no directory, fall back to input name.
    alias_norms are normalized strings accepted as 'this zoo' (name + aliases).
    """
    directory = data.get("directory", {})
    if not isinstance(directory, dict) or not directory:
        n = _norm_spaces(input_name)
        return input_name, {n}

    # Build map: normalized name/aliases -> canonical key
    norm_to_canon: dict[str, str] = {}
    for canon in directory.keys():
        canon_norm = _norm_spaces(canon)
        norm_to_canon[canon_norm] = canon  # main key
        rec = directory.get(canon, {})
        if isinstance(rec, dict) and isinstance(rec.get("aliases"), list):
            for a in rec["aliases"]:
                if isinstance(a, str) and a.strip():
                    norm_to_canon[_norm_spaces(a)] = canon

    want_norm = _norm_spaces(input_name)
    if want_norm in norm_to_canon:
        canon = norm_to_canon[want_norm]
        # collect all aliases that map to this canon
        alias_norms = {k for k, v in norm_to_canon.items() if v == canon}
        return canon, alias_norms

    # Fuzzy: try closests among canonical keys only
    choices = list(directory.keys())
    match = get_close_matches(input_name, choices, n=1, cutoff=0.85)
    if match:
        canon = match[0]
        # collect alias set for the chosen canon
        alias_norms = {k for k, v in norm_to_canon.items() if v == canon}
        alias_norms.add(_norm_spaces(canon))
        return canon, alias_norms

    # Fallback: keep input, alias set is just input normalized
    return input_name, {want_norm}


# ---------- Robust helpers for ;progress (handles casing + varied keys) ----------
_norm_re = re.compile(r"[^a-z0-9]+")

def _norm_zoo(name: str) -> str:
    return _norm_re.sub("", (name or "").lower()).strip()

def _build_canonical_zoo_map(data: dict) -> dict[str, str]:
    """
    {normalized -> Canonical Name}, prioritizing directory keys, then users' zoos, then users' collections.
    """
    canon: dict[str, str] = {}
    directory = data.get("directory") or {}

    for z in directory.keys():
        if isinstance(z, str):
            canon.setdefault(_norm_zoo(z), z)

    for _uid, urec in (data.get("users") or {}).items():
        for z in (urec.get("zoos") or {}).keys():
            if isinstance(z, str):
                canon.setdefault(_norm_zoo(z), z)
        for z in (urec.get("collections") or {}).keys():
            if isinstance(z, str):
                canon.setdefault(_norm_zoo(z), z)

    return canon


def _get_by_norm_key(obj: dict, target_key: str) -> tuple[str | None, Any]:
    """
    Find a value in 'obj' whose key, normalized, equals normalized(target_key).
    Returns (original_key, value) or (None, None).
    """
    want = _norm_zoo(target_key)
    for k, v in (obj or {}).items():
        if isinstance(k, str) and _norm_zoo(k) == want:
            return k, v
    return None, None


def _extract_species_set(raw) -> set[str]:
    """
    Accepts a list[str] / set[str] / tuple[str], or a dict[str, any] whose KEYS are species names.
    """
    if isinstance(raw, (list, set, tuple)):
        return {s for s in raw if isinstance(s, str) and s.strip()}
    if isinstance(raw, dict):
        return {s for s in raw.keys() if isinstance(s, str) and s.strip()}
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set()
    return set()


def _extract_directory_species(dentry: dict) -> set[str]:
    """
    Only read from explicit species containers inside the directory entry:
      'species', 'held', 'holdings', or 'collection'
    DO NOT fall back to using the directory entry's top-level keys as species.
    """
    if not isinstance(dentry, dict):
        return set()

    for key in ("species", "held", "holdings", "collection"):
        if key in dentry:
            s = _extract_species_set(dentry.get(key))
            if s:
                return s

    return set()


def _find_owner_uid_for_zoo(data: dict, canon_zoo: str) -> int | None:
    """
    Owner = user with the largest *housed* list for this zoo.
    If nobody houses anything for this zoo, return None.
    """
    best_uid = None
    best_count = 0

    for uid, urec in (data.get("users") or {}).items():
        zoos = urec.get("zoos") or {}
        _orig, housed_raw = _get_by_norm_key(zoos, canon_zoo)
        housed = _extract_species_set(housed_raw)

        if len(housed) > best_count:
            best_count = len(housed)
            best_uid = int(uid)

    return best_uid if best_count > 0 else None


def _get_housed_by_owner(data: dict, canon_zoo: str, owner_uid: int | None) -> set[str]:
    """
    Return the housed species for the detected owner of this zoo.
    """
    if owner_uid is None:
        return set()

    urec = (data.get("users") or {}).get(str(owner_uid)) or {}
    _k, housed_raw = _get_by_norm_key(urec.get("zoos") or {}, canon_zoo)
    return _extract_species_set(housed_raw)


def _get_held_for_zoo(data: dict, canon_zoo: str, owner_uid: int | None) -> set[str]:
    """
    Find all 'held' species for a zoo, searching all likely spots:
      1) directory[zoo]['species'] or ['holdings'] or ['collection'] or ['held']
      2) owner's collections[zoo]
      3) any user's collections[zoo]
      4) directory[zoo] directly if it looks like a dict of species
    """
    directory = data.get("directory") or {}
    held: set[str] = set()

    for dkey, dval in directory.items():
        if _norm_zoo(dkey) == _norm_zoo(canon_zoo):
            if isinstance(dval, dict):
                for k in ("species", "held", "holdings", "collection"):
                    if k in dval:
                        held |= _extract_species_set(dval[k])

                if not held and all(isinstance(v, (dict, str)) for v in dval.values()):
                    held |= _extract_species_set(dval)
            break

    if not held and owner_uid is not None:
        urec = (data.get("users") or {}).get(str(owner_uid)) or {}
        _ck, coll_raw = _get_by_norm_key(urec.get("collections") or {}, canon_zoo)
        held |= _extract_species_set(coll_raw)

    if not held:
        for _uid, urec in (data.get("users") or {}).items():
            _ck, coll_raw = _get_by_norm_key(urec.get("collections") or {}, canon_zoo)
            held |= _extract_species_set(coll_raw)

    return held

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

def resolve_institution_name(query: str):
    data = _load_zoo_data()
    directory = data.get("directory", {}) or {}

    q = query.strip().lower()
    if not q:
        return None, None

    # 1. Direct key match
    if q in directory:
        info = directory[q] or {}
        return info.get("name", q.title()), None

    # 2. Exact display-name match
    for key, info in directory.items():
        display = (info or {}).get("name", key)
        if display.strip().lower() == q:
            return display, None

    # 3. Loose partial match for suggestion
    for key, info in directory.items():
        display = (info or {}).get("name", key)
        if q in display.strip().lower() or display.strip().lower() in q:
            return None, display

    return None, None

def get_holdings_for_institution(inst_name: str):
    items = []
    total = 0
    inst_q = inst_name.strip().lower()

    for sp_key, entry in species_data.items():
        found_raw = None

        # First: check direct institutions map
        inst_map = entry.get("institutions") or {}
        if isinstance(inst_map, dict):
            for zoo_name, raw in inst_map.items():
                if str(zoo_name).strip().lower() == inst_q:
                    found_raw = raw
                    break

        # Second: fall back to regional holdings map
        if found_raw is None:
            holdings = entry.get("holdings") or {}
            if isinstance(holdings, dict):
                for region_val in holdings.values():
                    if not region_val or region_val == 0:
                        continue

                    if isinstance(region_val, str):
                        parts = [p.strip() for p in region_val.split(",") if p.strip()]
                        for part in parts:
                            if " - " in part:
                                qty, zoo = part.split(" - ", 1)
                                if zoo.strip().lower() == inst_q:
                                    found_raw = qty.strip()
                                    break
                        if found_raw is not None:
                            break

                    elif isinstance(region_val, list):
                        for part in region_val:
                            s = str(part).strip()
                            if " - " in s:
                                qty, zoo = s.split(" - ", 1)
                                if zoo.strip().lower() == inst_q:
                                    found_raw = qty.strip()
                                    break
                        if found_raw is not None:
                            break

        if found_raw is None:
            continue

        count = zims_to_count(found_raw)
        if count > 0:
            sci = entry.get("scientific", "")
            common = entry.get("common", sp_key)
            items.append((common, sci, found_raw, count))
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

def holdings_to_inline(value) -> str:
    """
    Format a region's holdings as a single inline string like:
    '1.1 [Blue], 0.1 [Yellow] - Jupiter Reptile Zoo'
    Handles list/str/dict forms gracefully.
    """
    if not value:
        return "—"

    items: list[str] = []

    if isinstance(value, dict):
        for inst, v in value.items():
            if isinstance(v, list):
                for s in v:
                    s = (s or "").strip()
                    if s:
                        items.append(s if inst and inst in s else (f"{s} - {inst}" if inst else s))
            elif isinstance(v, str):
                s = v.strip()
                if s:
                    items.append(s if inst and inst in s else (f"{s} - {inst}" if inst else s))

    elif isinstance(value, list):
        for s in value:
            if isinstance(s, str):
                s = s.strip()
                if s:
                    items.append(s)
            elif isinstance(s, dict):
                parts = []
                if s.get("count"): parts.append(str(s["count"]))
                if s.get("variant"): parts.append(f"[{s['variant']}]")
                label = " ".join(parts).strip()
                if s.get("inst"): label = f"{label} - {s['inst']}" if label else s["inst"]
                if label:
                    items.append(label)

    elif isinstance(value, str):
        items.append(value.strip())

    return ", ".join([i for i in items if i]) or "—"


# --- Helper: format a single region's holdings as bullets ---
def _format_region_holdings(val) -> str:
    """Turn holdings for a single region into a bulleted list."""
    if not val:
        return "_None_"

    # "3.0 - ECZ, 2.0 - HUZ" -> bullets
    if isinstance(val, str):
        parts = [p.strip() for p in val.split(",") if p.strip()]
        return "\n".join(f"• {p}" for p in parts) if parts else "_None_"

    # ["3.0 - ECZ", "2.0 - HUZ"] -> bullets
    if isinstance(val, list):
        items = [str(p).strip() for p in val if str(p).strip()]
        return "\n".join(f"• {p}" for p in items) if items else "_None_"

    # {"Essex County Zoo": "3.0", "High Uintahs Zoo": "2.0"} -> bullets
    if isinstance(val, dict):
        items = []
        for inst, count in val.items():
            if count in (0, "0", "", None):
                continue
            items.append(f"• {count} - {inst}")
        return "\n".join(items) if items else "_None_"

    # numbers or anything else
    if isinstance(val, (int, float)) and val > 0:
        return f"• {val}"
    return "_None_"


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

    # Info field
    if entry.get("info"):
        e.add_field(name="About", value=entry["info"], inline=False)

    # Holdings by region (render whatever keys exist, as bullets)
    holdings = entry.get("holdings") or {}
    any_listed = False

    for region, raw_val in holdings.items():
        if raw_val in (None, "", 0, "0", "0.0"):
            continue

        formatted = _format_region_holdings(raw_val)
        if formatted and formatted != "_None_":
            any_listed = True
            e.add_field(name=region, value=formatted, inline=False)

    if not any_listed:
        e.add_field(name="Holdings", value="No current reported holdings.", inline=False)

    # Images (debug version)
    images = entry.get("images") or []
    if isinstance(images, list) and len(images) > 0:
        idx = max(0, min(image_index, len(images) - 1))
        img = images[idx]
        raw_url = img.get("url")
        url = get_display_image_url(raw_url)

        print("SPECIES:", entry.get("common"))
        print("USING IMAGES LIST")
        print("RAW URL:", raw_url)
        print("CLEAN URL:", url)

        if url:
            e.set_image(url=url)
            print("SET IMAGE TO:", url)
            label = img.get("label", "Variant")
            e.set_footer(text=f"{label} • {idx + 1}/{len(images)}")
        else:
            print("IMAGE BLOCKED OR MISSING")
            label = img.get("label", "Variant")
            e.set_footer(text=f"{label} • {idx + 1}/{len(images)} • Image unavailable")
    else:
        raw_url = entry.get("image_url")
        url = get_display_image_url(raw_url)

        print("SPECIES:", entry.get("common"))
        print("USING SINGLE IMAGE_URL")
        print("RAW URL:", raw_url)
        print("CLEAN URL:", url)

        if url:
            e.set_image(url=url)
            print("SET IMAGE TO:", url)
        else:
            print("IMAGE BLOCKED OR MISSING")

    # ✅ THIS WAS MISSING
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

    async def _update_embed(self, interaction: discord.Interaction):
        embed = build_species_embed(self.entry, self.index)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.images:
            return await interaction.response.defer()
        self.index = (self.index - 1) % len(self.images)
        await self._update_embed(interaction)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.images:
            return await interaction.response.defer()
        self.index = (self.index + 1) % len(self.images)
        await self._update_embed(interaction)


# ==============================  ADDED: ZOO PROGRESS + OWNERSHIP  ==============================
# ---------- Persistence ----------
_ZOO_DATA_PATH = pathlib.Path(__file__).with_name("zoo_progress.json")

def _load_zoo_data() -> dict:
    if _ZOO_DATA_PATH.exists():
        try:
            data = json.loads(_ZOO_DATA_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            log.exception("zoo_progress.json is invalid JSON. NOT overwriting it.")
            data = {}
        except Exception:
            log.exception("Could not load zoo_progress.json. NOT overwriting it.")
            data = {}
    else:
        data = {}

    # Ensure buckets exist without wiping existing contents
    data.setdefault("users", {})
    data.setdefault("ownership", {})
    data.setdefault("directory", {})
    data.setdefault("contracept", {})
    data.setdefault("breeding_channels", {})
    data.setdefault("birth_log", [])
    data.setdefault("species_overrides", {})

    return data


def _save_zoo_data(data: dict) -> None:
    tmp_path = _ZOO_DATA_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    tmp_path.replace(_ZOO_DATA_PATH)

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
    "Karl Eberhard's Pine Lake Zoo",
    "Riverside Zoo",
    "Brussels Zoo",
    "Parque Zoologico de Clear Coast",
    "Essex County Zoo",
    "Tri-State Zoo & Aquarium",
    "Auckland Conservation Zoo and Aquarium",
    "Blue Seas Aquarium"
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

# ----------------------------- ;progress command --------------------------------------
@bot.command(name="progress")
async def cmd_progress(ctx: commands.Context):
    """
    ;progress
    Lists every known zoo and its housed percentage, using the same inputs your ;zoo view cards use:
    - Owner = user with most housed at that zoo
    - Held species = directory[zoo].species (fallback: owner's collections[zoo], then union of all collections[zoo])
    - Housed species = owner's zoos[zoo]
    """
    try:
        data = _load_zoo_data()

        # Build canonical names so we show properly-capitalized zoo names
        canon_map = _build_canonical_zoo_map(data)
        if not canon_map:
            await ctx.send("No zoos recorded yet.")
            return

        rows: list[tuple[str, int | None, int, int, float | None]] = []
        # (zoo_name, owner_uid, housed_count, held_count, pct)

        for _norm, zoo in sorted(canon_map.items(), key=lambda kv: kv[1].lower()):
            owner_uid = _find_owner_uid_for_zoo(data, zoo)
            housed_set = _get_housed_by_owner(data, zoo, owner_uid)
            held_set = _get_held_for_zoo(data, zoo, owner_uid)

            housed = len(housed_set)
            held = len(held_set)
            pct = (housed / held * 100.0) if held > 0 else None
            rows.append((zoo, owner_uid, housed, held, pct))

        # Sort by percentage desc, then by name
        rows.sort(key=lambda r: (-(r[4] if r[4] is not None else -1.0), r[0].lower()))

        # Build readable lines; keep list compact if very long
        lines: list[str] = []
        for zoo, owner_uid, housed, held, pct in rows:
            # Owner tag (optional)
            owner_tag = f" <@{owner_uid}>" if owner_uid is not None else ""
            if held == 0:
                lines.append(f"🏛️ **{zoo}** — _no holdings set_ (owner unknown){owner_tag}")
            else:
                pct_text = f"{pct:.1f}%" if pct is not None else "—"
                lines.append(f"🏛️ **{zoo}** — {housed}/{held} housed ({pct_text}){owner_tag}")

        # Chunk if needed (Discord 2000 char limit)
        header = "📊 **Zoo Housing Progress (based on directory/collections + owners from housed data)**"
        msg = header + "\n" + "\n".join(lines)
        if len(msg) <= 1900:
            await ctx.send(msg)
        else:
            # spill into a .txt for very big lists
            content = header + "\n\n" + "\n".join(lines)
            buf = io.BytesIO(content.encode("utf-8"))
            buf.seek(0)
            file = discord.File(buf, filename="zoo_progress.txt")
            await ctx.send("List was long—here’s a file:", file=file)

    except Exception:
        log.exception("Error in ;progress")
        await ctx.send("Sorry—couldn’t build the progress list. Check logs for details.")

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

        print("ZOO DATA PATH:", _ZOO_DATA_PATH)
        print("ZOO NAMES:", get_all_zoo_names())


# ---------- Ownership ----------
DEFAULT_ZOO_LIMIT = 2  # change this default if you like

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

def _extract_species_anyshape(value) -> list[str]:
    """Extremely permissive species extractor for collections/holdings blocks."""
    out: list[str] = []

    def add_str(x):
        if isinstance(x, str):
            s = x.strip()
            if s and not s.lower().startswith(("http://","https://")) and len(s) <= 150:
                out.append(s)

    def walk(o, depth=0):
        if o is None or depth > 8:
            return
        if isinstance(o, str):
            add_str(o); return
        if isinstance(o, (list, tuple, set)):
            for it in o: walk(it, depth+1)
            return
        if isinstance(o, dict):
            # keys might be species names
            for k, v in o.items():
                add_str(k)
                # values may be lists/dicts containing more names
                walk(v, depth+1)
            return
        # booleans / numbers ignored

    walk(value, 0)

    # dedupe case-insensitively
    seen = set(); uniq = []
    for s in out:
        ns = s.casefold()
        if ns not in seen:
            seen.add(ns); uniq.append(s)
    return uniq


def _iter_collection_containers(users_dict):
    """
    Yield (zoo_name, value) for every zoo in any user's collections-like container,
    no matter how it is structured or where it appears.
    """
    KEY_CANDIDATES = {"collections","collection","holdings"}
    def normz(s): 
        return re.sub(r"[^a-z0-9]+","",str(s).lower())

    def walk(o, under_key=None):
        if isinstance(o, dict):
            # If this dict itself is a collections-container, its keys are zoos
            if isinstance(under_key, str) and under_key.lower() in KEY_CANDIDATES:
                for zname, val in o.items():
                    yield (str(zname), val)
            # recurse
            for k, v in o.items():
                # pass the key downward so we know when we’re under a collections container
                yield from walk(v, k)
        elif isinstance(o, (list, tuple, set)):
            for it in o:
                yield from walk(it, under_key)
        # else: primitives ignored

    yield from walk(users_dict, None)


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

def _format_region_holdings(val) -> str:
    """Turn holdings for a single region into a bulleted list."""
    # "3.0 - ECZ, 2.0 - HUZ"  -> bullets
    if isinstance(val, str):
        parts = [p.strip() for p in val.split(",") if p.strip()]
        return "\n".join(f"• {p}" for p in parts) if parts else "_None_"

    # ["3.0 - ECZ", "2.0 - HUZ"] -> bullets
    if isinstance(val, list):
        items = [str(p).strip() for p in val if str(p).strip()]
        return "\n".join(f"• {p}" for p in items) if items else "_None_"

    # {"Essex County Zoo": "3.0", "High Uintahs Zoo": "2.0"} -> bullets
    if isinstance(val, dict):
        items = []
        for inst, count in val.items():
            if count in (0, "0", "", None):
                continue
            items.append(f"• {count} - {inst}")
        return "\n".join(items) if items else "_None_"

    # numbers or anything else
    if isinstance(val, (int, float)) and val > 0:
        return f"• {val}"
    return "_None_"


def _format_region_holdings(val) -> str:
    """Turn holdings for a single region into a bulleted list."""
    if not val:
        return "_None_"

    # "3.0 - ECZ, 2.0 - HUZ" -> bullets
    if isinstance(val, str):
        parts = [p.strip() for p in val.split(",") if p.strip()]
        return "\n".join(f"• {p}" for p in parts) if parts else "_None_"

    # ["3.0 - ECZ", "2.0 - HUZ"] -> bullets
    if isinstance(val, list):
        items = [str(p).strip() for p in val if str(p).strip()]
        return "\n".join(f"• {p}" for p in items) if items else "_None_"

    # {"Essex County Zoo": "3.0", "High Uintahs Zoo": "2.0"} -> bullets
    if isinstance(val, dict):
        items = []
        for inst, count in val.items():
            if count in (0, "0", "", None):
                continue
            items.append(f"• {count} - {inst}")
        return "\n".join(items) if items else "_None_"

    # numbers or anything else
    if isinstance(val, (int, float)) and val > 0:
        return f"• {val}"
    return "_None_"


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
    "very easy": 0.30,
    "easy": 0.20,
    "average": 0.10,
    "below average": 0.06,
    "difficult": 0.01,
    "impossible": 0.00,
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
    """Merge species_overrides without mutating the base."""
    data = _load_zoo_data()

    if not isinstance(data, dict):
        data = {}

    if not entry:
        return entry

    name = entry.get("common")
    overrides = data.get("species_overrides", {})

    if name and name in overrides:
        merged = dict(entry)
        merged.update(overrides[name])
        return merged

    return entry


def get_breeding_label(entry: dict) -> str:
    label = (entry or {}).get("breeding") or DEFAULT_BREEDING_LABEL
    return label if label.lower() in BREEDING_PROB else DEFAULT_BREEDING_LABEL


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

    # >>> NEW: migrate old names to canonical casing/keys so the new UI works everywhere
    if _migrate_canonicalize_zoos(data):
        _save_zoo_data(data)

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
    print("ZOO COMMAND TRIGGERED:", ctx.message.content)

    def _build_zoo_embed(ctx, zoo_name: str, data: dict):
        return _build_zoo_progress_embed(
            ctx,
            zoo_name,
            data,
            ctx.author.id,
            category="housed",
            page=0
        )

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

    if sub == "view":
        print("ZOO VIEW TRIGGERED:", ctx.message.content)

        if rest and rest.strip():
            print("STEP 1: got rest")
            print("RAW REST:", repr(rest.strip()))
            print("DIRECTORY DATA:", _load_zoo_data().get("directory", {}))
            exact, suggestion = resolve_institution_name(rest.strip())
            print("STEP 2:", exact, suggestion)

            if not exact and suggestion:
                await ctx.send(f"No exact entry for **{rest.strip()}**. Did you mean **{suggestion}**?")
                return
            if not exact and not suggestion:
                await ctx.send(f"No institutions recorded yet or no match for **{rest.strip()}**.")
                return
            target_zoo = exact
            print("STEP 3: target_zoo =", target_zoo)
        else:
            print("STEP 4: no rest provided")
            target_zoo, err = _auto_pick_owned_or_msg(ownership["zoos"], None, need_ownership=False)
            print("STEP 5:", target_zoo, err)
            if err:
                await ctx.send("Please provide a zoo name to view (e.g., `;zoo view Mint Park Zoo`).")
                return

        print("STEP 6: checking ownership")
        viewer_owns = any(_norm_zoo(z) == _norm_zoo(target_zoo) for z in ownership["zoos"])
        print("STEP 7: viewer_owns =", viewer_owns)

        e = _build_zoo_progress_embed(ctx, target_zoo, data, ctx.author.id, category="housed", page=0)
        view = ZooViewPager(ctx, target_zoo, data, start_category="housed", start_page=0)
        await ctx.send(embed=e, view=view)

        print("STEP 12: done")
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
    key = (ctx.command.name, ctx.message.id)
    now = time.time()

    if key in _seen_messages and now - _seen_messages[key] < 10:
        return

    _seen_messages[key] = now
    print(f"COMMAND FIRED: {ctx.command.name} message_id={ctx.message.id}")
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

        print("FALLING BACK TO NORMAL EMBED SEND")
        print("EMBED TYPE:", type(embed))
        print("EMBED DICT:", embed.to_dict())

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
    Outputs the holdings list as a .txt file instead of inline messages.
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
        # If format_institution_holdings returns an error string, send that directly
        if isinstance(blocks, str):
            await ctx.send(blocks)
            return

        # Combine all blocks into one text file
        combined_text = f"Holdings for {exact} ({sp_count} species, {total} total individuals)\n\n"
        combined_text += "\n".join(blocks)

        # Encode and send as .txt
        import io
        buf = io.BytesIO(combined_text.encode("utf-8"))
        buf.seek(0)
        file = discord.File(buf, filename=f"{exact.replace(' ', '_')}_holdings.txt")
        await ctx.send(f"Here’s a text file with all holdings for **{exact}**:", file=file)

    except Exception:
        log.exception("Error in ;holdings")
        await ctx.send(f"Sorry, something went wrong looking up holdings for **{institution}**.")

def _load_species_data() -> dict[str, dict[str, Any]]:
    return species_data

@bot.command(name="orderat", aliases=["zooorder", "orderheld"])
async def cmd_order_at(ctx, order: str = None, *, zoo: str = None):
    """
    Usage:
    ;orderat <order> <zoo>
    Shows all animals in the given taxonomic order that the specified zoo holds.
    Example:
      ;orderat Carnivora Cube Zoological Park
    """
    if not order or not zoo:
        return await ctx.send("Usage: `;orderat <order> <zoo>`")

    try:
        # --- Normalize and resolve zoo name ---
        try:
            exact, suggestion = resolve_institution_name(zoo)
        except NameError:
            exact, suggestion = zoo, None

        if not exact and suggestion:
            return await ctx.send(f"No exact entry for **{zoo}**. Did you mean **{suggestion}**?")
        if not exact and not suggestion:
            return await ctx.send(f"No institutions recorded yet or no match for **{zoo}**.")

        order_q = order.strip().lower()
        zoo_q = (exact or zoo).strip().lower()

        # --- Load species data ---
        try:
            species_data = _load_species_data()
        except NameError:
            try:
                species_data = SPECIES  # fallback
            except NameError:
                return await ctx.send("Couldn’t load species registry (missing `_load_species_data()` or `SPECIES`).")

        matches = []

        # --- Scan all species for matching order and zoo holdings ---
        for sp_name, entry in species_data.items():
            sp_order = str(entry.get("order", "")).strip().lower()
            if sp_order != order_q:
                continue

            holdings = entry.get("holdings") or {}
            if not isinstance(holdings, dict) or not holdings:
                continue

            # Look for zoo in holdings
            found = False
            for holders in holdings.values():
                if not holders or holders == 0:
                    continue
                if isinstance(holders, str):
                    parts = [p.strip() for p in holders.split(",") if p.strip()]
                    if any(zoo_q in p.lower() for p in parts):
                        found = True
                        break
                elif isinstance(holders, list):
                    if any(zoo_q in str(h).lower() for h in holders):
                        found = True
                        break

            if found:
                common = entry.get("common", sp_name)
                sci = entry.get("scientific", "")
                matches.append((common, sci))

        if not matches:
            return await ctx.send(
                f"No animals from order **{order.title()}** recorded for **{(exact or zoo).title()}**."
            )

        # --- Alphabetize by common name ---
        matches.sort(key=lambda x: x[0].lower())

        # --- Build embed ---
        lines = [f"• **{c}** (*{s}*)" if s else f"• **{c}**" for c, s in matches]
        header = f"**Animals of Order {order.title()} in {(exact or zoo).title()}**"
        body = "\n".join(lines)

        # Split if too long for one embed
        if len(body) > 3900:
            await ctx.send(header)
            chunk = []
            total = 0
            for line in lines:
                if total + len(line) + 1 > 1900:
                    await ctx.send("\n".join(chunk))
                    chunk, total = [], 0
                chunk.append(line)
                total += len(line) + 1
            if chunk:
                await ctx.send("\n".join(chunk))
        else:
            embed = discord.Embed(
                title=f"Animals of Order {order.title()}",
                description=f"**Zoo:** {(exact or zoo).title()}\n\n" + body,
                color=0x2ECC71,
            )
            await ctx.send(embed=embed)

    except Exception:
        log.exception("Error in ;orderat")
        await ctx.send("Sorry, something went wrong while gathering that order list.")




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

        # Try to resolve as a specific species first
        entry, msg = get_entry_or_message(name)
        if not msg:
            value = entry.get("type")
            if value:
                await ctx.send(f"**{entry['common']}** is a **{value}**.")
            else:
                await ctx.send(f"No type information stored for **{entry['common']}**.")
            return

        # Not a species; interpret as a type category and export to .txt
        display, species_names = match_type_or_order(name, field="type")
        if display and species_names:
            species_sorted = builtins.sorted(species_names, key=lambda s: s.lower())
            header = f"Species in type: {display}\nTotal: {len(species_sorted)}\n\n"
            content = header + "\n".join(species_sorted)

            # Make a simple, safe filename from the type
            slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in display.strip().lower())
            if not slug:
                slug = "type"

            buf = io.BytesIO(content.encode("utf-8"))
            buf.seek(0)
            file = discord.File(buf, filename=f"type_{slug}.txt")
            await ctx.send(f"Here’s a text file for **{display}**:", file=file)
            return

        # Fallthrough: show whatever helpful message came from species lookup
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
    if isinstance(error, commands.CommandNotFound):
        return
    log.exception("Command error: %s", error)
    await ctx.send("An error occurred while processing that command.")

# --- Ready / Run -------------------------------------------------------------
@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    log.info("Bot is ready.")

    # Start scheduled tasks
    if not weekly_breeding.is_running():
        weekly_breeding.start()
    if not progress_role_sweeper.is_running():
        progress_role_sweeper.start()

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
@bot.command(name="commands", aliases=["h"])
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
import random
from datetime import time as dtime
from zoneinfo import ZoneInfo
from discord.ext import tasks

def _nyc_time(hour: int, minute: int = 0) -> dtime:
    tz = ZoneInfo("America/New_York")
    return dtime(hour=hour, minute=minute, tzinfo=tz)


def _iter_housed_by_user_and_zoo():
    """
    Yields (user_id:int, zoo_name:str, species_list:List[str])
    for every housed species in every zoo.
    """
    data = _load_zoo_data()
    for uid, urec in data.get("users", {}).items():
        for zoo_name, species_list in (urec.get("zoos") or {}).items():
            yield int(uid), zoo_name, list(species_list or [])

import random

def _apply_birth(user_id: int, zoo_name: str, entry: dict):
    """
    Adds ONE birth to the correct zoo inside the species' holdings.
    Births receive a RANDOM sex (50/50 male or female).

    Supported formats:
      - "1.1 - Zoo"
      - "0.3 - ZooA, 1.2 - ZooB"
      - ["1.1 - Zoo", "0.3 - Other Zoo"]
      - Zero values: 0, "0"
    """
    species_name = entry.get("common")
    if not species_name:
        return

    data = _load_zoo_data()
    cat = data.get("species") or {}
    raw = cat.get(species_name)  # species block
    if not raw:
        return

    holdings = raw.get("holdings") or {}
    changed = False

    # choose sex: True = male, False = female
    is_male = (random.random() < 0.5)

    def update_line(line: str) -> str:
        """
        Update a single 'a.b - Zoo' entry for this birth.
        """
        nonlocal changed, is_male

        line = line.strip()
        if " - " not in line:
            return line

        count_str, inst = line.split(" - ", 1)
        if inst.strip().lower() != zoo_name.lower():
            return line

        # must be "a.b" format
        if "." not in count_str:
            return line
        try:
            male, female = map(int, count_str.split("."))
        except Exception:
            return line

        # apply random sex
        if is_male:
            male += 1
        else:
            female += 1

        changed = True
        return f"{male}.{female} - {inst}"

    # loop all regions
    for region, v in holdings.items():

        # Zero/null case
        if v in (0, "0", None, 0.0):
            continue

        # list format
        if isinstance(v, (list, tuple, set)):
            new_list = []
            for line in v:
                before = line
                after = update_line(line)
                new_list.append(after)
            holdings[region] = new_list
            continue

        # string format (possibly multiple entries)
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",") if p.strip()]
            new_parts = []
            for part in parts:
                after = update_line(part)
                new_parts.append(after)

            holdings[region] = ", ".join(new_parts) if new_parts else "0"
            continue

        # anything else → skip
        continue

    if changed:
        raw["holdings"] = holdings
        data["species"] = cat
        _save_zoo_data(data)



async def _run_breeding_once() -> list[str]:
    """
    Runs ONE breeding cycle and returns a list of log lines.
    """
    lines: list[str] = []

    for user_id, zoo_name, species_list in _iter_housed_by_user_and_zoo():
        if not species_list:
            continue

        lines.append(f"👤 <@{user_id}> — {zoo_name}")
        housed_count = len(species_list)
        lines.append(f"• Housed: {housed_count}")

        for sp in species_list:
            entry, msg = get_entry_or_message(sp)
            if msg or not entry:
                continue

            entry = get_species_with_overrides(entry)
            common = entry.get("common", sp)

            # contraception
            if is_contracepted(user_id, zoo_name, common):
                lines.append(f"  🚫 {common} is contracepted → skipped.")
                continue

            # 1.1 or 3+ unsexed requirement
            if not _has_breeding_pair_or_group(zoo_name, entry):
                lines.append(f"  ⛔ {common} lacks 1.1 or 3+ unsexed → skipped.")
                continue

            # difficulty override
            override_label = (entry.get("breeding") or "").strip().lower()
            p = BREEDING_PROB.get(override_label, 0.0)

            if p <= 0:
                lines.append(f"  0️⃣ {common} difficulty Impossible (p=0) → skipped.")
                continue

            # roll RNG
            roll = random.random()
            if roll <= p:
                lines.append(f"  🎉 {common} → **BIRTH!** (p={p:.2f}, roll={roll:.3f})")
                _apply_birth(user_id, zoo_name, entry)
            else:
                lines.append(f"  🎲 {common} diff {entry.get('breeding')} (p={p:.2f}) roll={roll:.3f} → no")

        lines.append("")  # spacer between zoos

    return lines


# ---------------- Task Scheduler ----------------

@tasks.loop(time=_nyc_time(14, 0))  # 14:00 = 2 PM NY time
async def weekly_breeding():
    """
    Runs the breeding cycle automatically.
    This fires every day at 2 PM NY time, but only *does work* on Fridays.
    """
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    # Monday = 0, Tuesday = 1, ..., Friday = 4, Sunday = 6
    if now_ny.weekday() != 4:  # 4 = Friday
        return  # Not Friday → skip

    lines = await _run_breeding_once()
    msg = "\n".join(lines)

    for guild in bot.guilds:
        ch_id = get_breeding_channel_for_guild(guild.id)
        if not ch_id:
            continue
        ch = guild.get_channel(ch_id)
        if ch:
            chunks = [msg[i:i+1900] for i in range(0, len(msg), 1900)]
            for ck in chunks:
                await ch.send(f"📣 **Weekly Breeding Report**\n{ck}")

@weekly_breeding.before_loop
async def before_weekly():
    await bot.wait_until_ready()

@bot.command(name="breedrun")
@commands.has_permissions(administrator=True)
async def breedrun_cmd(ctx):
    """
    Run a manual breeding cycle now.
    Produces the same output format as the weekly breeding report.
    """
    await ctx.send("⏳ Running breeding simulation…")

    try:
        lines = await _run_breeding_once()

        if not lines:
            await ctx.send(
                "⚠️ Breeding ran, but no output was produced.\n"
                "Use `;breeddebug` to see why no species qualified."
            )
            return

        msg = "\n".join(lines)

        # chunk for Discord limit
        chunks = [msg[i:i+1900] for i in range(0, len(msg), 1900)]

        for i, ck in enumerate(chunks, start=1):
            header = "📣 **Manual Breeding Report**"
            if len(chunks) > 1:
                header += f" (Part {i}/{len(chunks)})"
            await ctx.send(f"{header}\n{ck}")

    except Exception as e:
        log.exception("Error in ;breedrun")
        await ctx.send(f"❌ Error during breeding run: `{e}`")


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
        skipped_pairgroup = 0  # <<< NEW: missing 1.1 or 3+ unsexed
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

                    # <<< NEW: require 1.1 or 3+ unsexed at THIS institution
                    if not _has_breeding_pair_or_group(zoo_name, entry):
                        skipped_pairgroup += 1
                        lines.append(f"  ⛔ `{cname}` lacks **1.1 or 3+ unsexed** at **{zoo_name}** → skipped.")
                        continue

                    # >>> CHANGED: reflect species-level override in debug
                    override_label = (entry.get("breeding") or "").strip()
                    if override_label:
                        label = override_label
                    else:
                        label = get_breeding_label(entry) or DEFAULT_BREEDING_LABEL

                    prob = BREEDING_PROB.get(label, BREEDING_PROB[DEFAULT_BREEDING_LABEL])
                    if prob <= 0:
                        skipped_prob0 += 1
                        lines.append(f"  0️⃣ `{cname}` difficulty **{label}** (p=0){' [override]' if override_label else ''} → skipped.")
                        continue

                    rolled += 1
                    roll = random.random()
                    ok = roll <= prob
                    if ok:
                        hits += 1
                    lines.append(
                        f"  🎲 `{cname}` diff **{label}**{' [override]' if override_label else ''} "
                        f"p={prob:.2f} roll={roll:.3f} → {'BIRTH' if ok else 'no'}"
                    )

        summary = (
            "\n— Summary —\n"
            f"Users:{total_users} Zoos:{total_zoos} Candidates:{total_candidates}\n"
            f"Rolled:{rolled} Hits:{hits} | Skipped: resolve={skipped_resolve}, "
            f"not_in_catalog={skipped_not_in_catalog}, contracept={skipped_contra}, "
            f"pair/group={skipped_pairgroup}, p0={skipped_prob0}"
        )
        text = "\n".join(lines) + summary

        # Chunk for Discord limits
        for i in range(0, len(text), 1900):
            await ctx.send(text[i:i+1900])

    except Exception as e:
        log.exception("breeddebug failed")
        await ctx.send(f"⚠️ breeddebug crashed: `{type(e).__name__}` — {e}")


# ---------------- Breeding pair/group requirement ----------------
_INAME_CLEANER = re.compile(r"\s+")

def _norm_institution_name(name: str) -> str:
    return _INAME_CLEANER.sub(" ", (name or "").strip().lower())

def _parse_inst_count_piece(piece: str) -> tuple[Optional[str], Optional[str]]:
    """
    Best-effort parse for lines like:
      '2.1 - Essex County Zoo'   OR   'Essex County Zoo - 2.1'
      '0.0.3 - New York Aquarium' OR 'New York Aquarium - 0.0.3'
      '3 unsexed - Cube Zoological Park'
    Returns (institution, count_str) or (None, None) if not parseable.
    """
    s = (piece or "").strip()
    if not s:
        return None, None

    # Try COUNT - INST
    m = re.match(r"^\s*(?P<count>[A-Za-z0-9 .]+?)\s*[-–—]\s*(?P<inst>.+?)\s*$", s)
    if m:
        return m.group("inst").strip(), m.group("count").strip()

    # Try INST - COUNT
    m = re.match(r"^\s*(?P<inst>.+?)\s*[-–—]\s*(?P<count>[A-Za-z0-9 .]+?)\s*$", s)
    if m:
        return m.group("inst").strip(), m.group("count").strip()

    # If there's no dash, we can't confidently split a freeform string
    return None, None

def _iter_inst_counts_for_species(entry: dict, target_inst: str) -> list[str]:
    """
    Search entry['holdings'] across all regions and return every raw count string
    that matches the given institution name.
    Robust to dict/list/str shapes.
    """
    out: list[str] = []
    holdings = (entry or {}).get("holdings") or {}
    if not isinstance(holdings, dict):
        return out

    target_norm = _norm_institution_name(target_inst)

    for _region, val in holdings.items():
        if not val:
            continue

        # Direct dict: {"Essex County Zoo": "2.1", ...}
        if isinstance(val, dict):
            for inst, count in val.items():
                if _norm_institution_name(str(inst)) == target_norm:
                    if count not in (None, "", 0, "0"):
                        out.append(str(count).strip())
            continue

        # List of pieces: ["2.1 - Zoo", "0.0.3 - Other"]
        if isinstance(val, list):
            for piece in val:
                inst, count = _parse_inst_count_piece(str(piece))
                if inst and count and _norm_institution_name(inst) == target_norm and count not in (None, "", "0", 0):
                    out.append(count.strip())
            continue

        # Comma/semicolon separated string
        if isinstance(val, str):
            parts = [p.strip() for p in re.split(r"[;,]\s*", val) if p.strip()]
            for piece in parts:
                inst, count = _parse_inst_count_piece(piece)
                if inst and count and _norm_institution_name(inst) == target_norm and count not in (None, "", "0", 0):
                    out.append(count.strip())
            continue

        # Fallback: ignore other shapes

    return out

def _breeding_ok_from_count(count_str: str) -> bool:
    """
    True iff the count string indicates:
      • at least 1.1 (male >=1 AND female >=1), OR
      • a breeding group of 3+ unsexed (e.g., '0.0.3', '3 unsexed', '3').
    Accepts 'm.f', 'm.f.u' formats, or plain integers.
    """
    if not count_str:
        return False

    s = count_str.strip().lower()

    # e.g., "3 unsexed"
    m = re.search(r"\b(\d+)\s*unsexed\b", s)
    if m:
        return int(m.group(1)) >= 3

    # Dot formats: m.f or m.f.u
    if re.fullmatch(r"\d+(?:\.\d+){1,2}", s):
        parts = [int(p) for p in s.split(".")]
        # m.f
        if len(parts) == 2:
            m_, f_ = parts
            return (m_ >= 1 and f_ >= 1)
        # m.f.u
        if len(parts) == 3:
            m_, f_, u_ = parts
            return (m_ >= 1 and f_ >= 1) or (u_ >= 3)

    # Plain integer -> treat as unsexed total
    if s.isdigit():
        return int(s) >= 3

    return False

def _has_breeding_pair_or_group(zoo_name: str, entry: dict) -> bool:
    """
    Check if THIS institution (zoo_name) has a qualifying sex ratio/group for the species entry.
    """
    for raw in _iter_inst_counts_for_species(entry, zoo_name):
        if _breeding_ok_from_count(raw):
            return True
    return False

@bot.command(name="progressrole.set")
@PROGRESS_CMD_PERMS
async def cmd_progressrole_set(ctx: commands.Context, role: discord.Role):
    """Bind the '≥50% housed' role for this server."""
    await _set_progress_role(ctx.guild, role)
    await ctx.send(f"✅ Set the progress role to {role.mention} for this server.")
    # Optional: immediate reconcile
    await recompute_progress_role_for_guild(ctx.guild)
    await ctx.send("🔄 Recomputed current assignments.")

    @bot.command(
        name="progressrole.check",
        aliases=["progressrolecheck", "progresscheck", "prcheck"]
    )
    async def cmd_progressrole_check(ctx: commands.Context, member: Optional[discord.Member] = None):
        """
        Check if you (or a specified member) currently qualify (≥50% housed in any owned zoo).
        Usage:
          ;progressrole.check
          ;progressrole.check @someone
        """
        m = member or ctx.author
        ok = any_zoo_over_50_for_user(m.id)
        await ctx.send(
            f"{m.mention} {'✅ qualifies' if ok else '❌ does not qualify'} (≥50% in any owned zoo)."
        )

    @bot.command(name="progressrole.refresh")
    @commands.has_permissions(manage_roles=True)
    async def cmd_progressrole_refresh(ctx: commands.Context):
        """Force a full recompute now."""
        await recompute_progress_role_for_guild(ctx.guild)
        await ctx.send("🔄 Refreshed role assignments.")

@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    log.info("Bot is ready.")

    if not weekly_breeding.is_running():
        weekly_breeding.start()
    if not progress_role_sweeper.is_running():
        progress_role_sweeper.start()

if __name__ == "__main__":
    # >>> ADDED: start keep-alive web server before running the bot <<<

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN not set in environment or .env")
        sys.exit(1)
    bot.run(token)