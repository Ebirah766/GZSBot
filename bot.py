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

# Order is optional; adjust to your project’s standard:
REGION_ORDER = ["North America", "South America", "Europe", "Asia", "Africa", "Oceania", "Antarctica"]

def format_holdings_lines(holdings: Dict[str, Any]) -> str:
    """
    Render holdings with one line per holder, not comma-separated.
    Accepts values like:
      - 0, "0", 0.0
      - "1.1 - Zoo A, 0.3 - Zoo B"  -> split into bullets
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
        "image_url": "https://cdn.britannica.com/09/225209-050-5002E7F8/Burmese-python-invasive-species-captured-Everglades-National-Park-Florida.jpg",
        "region": "Asia",
        "holdings": {
            "North America": "0.1 - Cube Zoological Park",
            "Asia": 0,
            "Europe": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Cube Zoological Park": "0.1"
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
        "region": "Europe, Asia",
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
            "North America": "1.0 [Wild type], 0.1 [Leucistic] - Jupiter Reptile Zoo",
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
    title = entry.get("common", "Unknown")
    sci = entry.get("scientific", "Unknown")
    e = discord.Embed(title=title, description=f"*{sci}*", color=discord.Color.blurple())
    for label in ["Type", "Order", "Family", "Genus"]:
        val = entry.get(label.lower())
        if val:
            e.add_field(name=label, value=val, inline=True)

    # <<< NEW: Regions line drawn from user-maintained entry['region'] >>>
    region_list = _region_list(entry)
    region_text = ", ".join(region_list) if region_list else "_None set_"
    e.add_field(name="Region(s)", value=region_text, inline=False)

    if entry.get("info"):
        e.add_field(name="About", value=entry["info"], inline=False)

    # --- Holdings by region (bulleted) ---
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
            return json.loads(_ZOO_DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # >>> NEW: add "directory" bucket for metadata like location
    return {"users": {}, "ownership": {}, "directory": {}}

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


# ------------- ;zoo command with ownership -------------
@bot.command(name="zoo")
async def zoo_cmd(ctx, subcommand: str = None, *, rest: str = None):
    """
    ;zoo set <name>         -> set your active zoo (must own it)
    ;zoo status             -> show current zoo list and progress
    ;zoo clear              -> clear your active zoo (keeps data)
    ;zoo list               -> list your local 'data buckets' (not ownership)
    ;zoo myzoos             -> show zoos you OWN + your limit usage
    ;zoo view [name]        -> UI card for active zoo or provided name
    ;zoo meta location <text>      -> set location for your active zoo
    ;zoo meta image <url>          -> set thumbnail image for your active zoo

    Admin subcommands:
    ;zoo owner add @user <zoo>
    ;zoo owner remove @user <zoo>
    ;zoo owner limit @user <n>
    ;zoo owner list [@user]
    """
    data = _load_zoo_data()
    user = _ensure_user_struct(data, ctx.author.id)
    ownership = _get_user_ownership(data, ctx.author.id)

    if subcommand is None:
        await ctx.send(
            "Usage:\n"
            "`;zoo set <name>`, `;zoo status`, `;zoo clear`, `;zoo list`, `;zoo myzoos`, `;zoo view [name]`\n"
            "`;zoo meta location <text>` • `;zoo meta image <url>`\n"
            "**Admin:** `;zoo owner add @user <zoo>`, `;zoo owner remove @user <zoo>`, "
            "`;zoo owner limit @user <n>`, `;zoo owner list [@user]`"
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

    # >>> NEW: meta tools (location/image) ------------------------------------
    if sub == "meta":
        if not rest:
            await ctx.send("Usage: `;zoo meta location <text>` • `;zoo meta image <url>`")
            return

        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            await ctx.send("Usage: `;zoo meta location <text>` • `;zoo meta image <url>`")
            return
        field, value = parts[0].lower(), parts[1].strip()

        # must have an active zoo you own
        zoo, msg = _get_active_zoo_or_msg(ctx, user)
        if msg:
            await ctx.send(msg); return
        if not _owns_zoo(ownership, zoo):
            await ctx.send(f"🚫 You don’t own **{zoo}**. Switch with `;zoo set <owned zoo>`.")
            return

        if field not in ("location", "image"):
            await ctx.send("Unknown meta field. Use `location` or `image`.")
            return

        data = _load_zoo_data()
        _set_zoo_meta_field(data, zoo, "location" if field == "location" else "image_url", value)
        _save_zoo_data(data)
        await ctx.send(f"✅ Updated **{field}** for **{zoo}**.")
        return

    # >>> NEW: view UI card ----------------------------------------------------
    # >>> NEW: view UI card ----------------------------------------------------
    if sub == "view":
        target_zoo = None
        if rest and rest.strip():
            # Validate provided zoo name against known institutions
            exact, suggestion = resolve_institution_name(rest.strip())
            if not exact and suggestion:
                await ctx.send(f"No exact entry for **{rest.strip()}**. Did you mean **{suggestion}**?")
                return
            if not exact and not suggestion:
                await ctx.send(f"No institutions recorded yet or no match for **{rest.strip()}**.")
                return
            target_zoo = exact  # use canonical/cased institution name
        else:
            # Fall back to active zoo (unchanged behavior)
            z, msg = _get_active_zoo_or_msg(ctx, user)
            if msg:
                await ctx.send(msg); return
            target_zoo = z

        # allow viewing even if you don't own it; UI will still show owner(s)
        e = _build_zoo_embed(ctx, target_zoo, data)
        await ctx.send(embed=e)
        return

    # --- regular subcommands (enforced) ---
    if sub == "set":
        if not rest:
            await ctx.send("Give your zoo a name: `;zoo set Mint Park Zoo`")
            return
        requested = " ".join(rest.split())
        if not _owns_zoo(ownership, requested):
            await ctx.send(f"🚫 You don’t own **{requested}**. Ask an admin to grant ownership with `;zoo owner add @you {requested}`.")
            return
        cased = _find_cased_zoo_name(ownership, requested) or requested
        user["active_zoo"] = cased
        user["zoos"].setdefault(cased, [])
        _save_zoo_data(data)
        await ctx.send(f"✅ Active zoo set to **{cased}**.")
        return

    if sub == "status":
        zoo, msg = _get_active_zoo_or_msg(ctx, user)
        if msg:
            await ctx.send(msg); return
        if not _owns_zoo(ownership, zoo):
            await ctx.send(f"🚫 You no longer own **{zoo}**. Pick a zoo you own with `;zoo set <name>`.")
            return
        housed = user["zoos"].get(zoo, [])
        housed_valid = [s for s in housed if _canonical_species_name(s)]
        # >>> CHANGED: denominator is species cataloged for this zoo
        catalog_set = _catalog_species_for_zoo(zoo)
        denom = len(catalog_set)
        num = len([s for s in housed_valid if s in catalog_set])
        pct = _percent(num, denom)
        bar_len = 20
        filled = round(pct / 100 * bar_len)
        bar = "█" * filled + "—" * (bar_len - filled)
        housed_preview = ", ".join(housed_valid[:20]) + (" …" if len(housed_valid) > 20 else "")
        await ctx.send(
            f"**{zoo}** — {num}/{denom} cataloged species housed ({pct:.1f}%)\n"
            f"`{bar}`\n"
            f"**Housed:** {housed_preview if housed_valid else '_None yet_'}"
        )
        return

    if sub == "clear":
        user["active_zoo"] = None
        _save_zoo_data(data)
        await ctx.send("Cleared your active zoo. Set a new one with `;zoo set <name>`.")
        return

    if sub == "list":
        zoos = list(user["zoos"].keys())
        if not zoos:
            await ctx.send("You don’t have any zoo data yet. Create data by `;zoo set <owned zoo>` then `;house ...`.")
            return
        await ctx.send("Your zoo data buckets:\n- " + "\n- ".join(zoos))
        return

    await ctx.send("Unknown subcommand. Try `;zoo set <name>`, `;zoo status`, `;zoo clear`, `;zoo list`, `;zoo myzoos`, `;zoo view [name]`, `;zoo meta ...`, or admin `;zoo owner ...`.")

# ------------- ;house / ;unhouse -------------
@bot.command(name="house")
async def house_cmd(ctx, *, species_name: str = None):
    """
    Add a species to your active zoo’s housed list (ONLY if your zoo actually holds it).
    Usage: ;house Whale Shark
    """
    if not species_name:
        await ctx.send("Usage: `;house <species name>` (e.g., `;house Whale Shark`)")
        return

    data = _load_zoo_data()
    user = _ensure_user_struct(data, ctx.author.id)
    ownership = _get_user_ownership(data, ctx.author.id)

    zoo, msg = _get_active_zoo_or_msg(ctx, user)
    if msg:
        await ctx.send(msg); return
    if not _owns_zoo(ownership, zoo):
        await ctx.send(f"🚫 You don’t own **{zoo}**. Switch with `;zoo set <owned zoo>`.")
        return

    canonical = _canonical_species_name(species_name)
    if not canonical:
        await ctx.send(f"❌ I don’t recognize **{species_name}**. Make sure it’s in the catalog.")
        return

    # >>> NEW: enforce 'in holdings' check for this zoo
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
        # progress uses zoo catalog denominator (unchanged)
        denom = len(catalog_set)
        num = len([s for s in housed if _canonical_species_name(s) and s in catalog_set])
        pct = _percent(num, denom)
        await ctx.send(f"✅ Added **{canonical}** to **{zoo}**. Progress: {num}/{denom} ({pct:.1f}%)")

@bot.command(name="unhouse")
async def unhouse_cmd(ctx, *, species_name: str = None):
    """
    Remove a species from your active zoo’s housed list.
    Usage: ;unhouse Whale Shark
    """
    if not species_name:
        await ctx.send("Usage: `;unhouse <species name>`")
        return

    data = _load_zoo_data()
    user = _ensure_user_struct(data, ctx.author.id)
    ownership = _get_user_ownership(data, ctx.author.id)

    zoo, msg = _get_active_zoo_or_msg(ctx, user)
    if msg:
        await ctx.send(msg); return
    if not _owns_zoo(ownership, zoo):
        await ctx.send(f"🚫 You don’t own **{zoo}**. Switch with `;zoo set <owned zoo>`.")
        return

    canonical = _canonical_species_name(species_name)
    if not canonical:
        await ctx.send(f"❌ I don’t recognize **{species_name}**.")
        return

    housed = user["zoos"].setdefault(zoo, [])
    if canonical in housed:
        housed.remove(canonical)
        _save_zoo_data(data)
        # use zoo catalog denominator for progress
        catalog_set = _catalog_species_for_zoo(zoo)
        denom = len(catalog_set)
        num = len([s for s in housed if _canonical_species_name(s) and s in catalog_set])
        pct = _percent(num, denom)
        await ctx.send(f"✅ Removed **{canonical}** from **{zoo}**. Progress: {num}/{denom} ({pct:.1f}%)")
    else:
        await ctx.send(f"ℹ️ **{canonical}** isn’t currently housed at **{zoo}**.")

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

# ==============================  TOKENS SYSTEM  ==============================
_TOKENS_PATH = pathlib.Path(__file__).with_name("tokens.json")
DEFAULT_TOKENS = 10

def _load_tokens() -> dict:
    if _TOKENS_PATH.exists():
        try:
            return json.loads(_TOKENS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"balances": {}}  # { "balances": { "<user_id>": int } }

def _save_tokens(data: dict) -> None:
    _TOKENS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _ensure_balance(data: dict, user_id: int) -> int:
    bal = data.setdefault("balances", {}).get(str(user_id))
    if bal is None:
        data["balances"][str(user_id)] = DEFAULT_TOKENS
        _save_tokens(data)
        return DEFAULT_TOKENS
    return int(bal)

def _set_balance(data: dict, user_id: int, new_val: int) -> int:
    new_val = max(0, int(new_val))
    data.setdefault("balances", {})[str(user_id)] = new_val
    _save_tokens(data)
    return new_val

def _add_balance(data: dict, user_id: int, delta: int) -> int:
    cur = _ensure_balance(data, user_id)
    return _set_balance(data, user_id, cur + int(delta))

# ---- Commands ----
@bot.command(name="tokens")
async def tokens_cmd(ctx, member: discord.Member = None):
    """
    Check token balance.
    - ;tokens             -> your balance
    - ;tokens @user       -> admin can view others
    """
    target = member or ctx.author
    if member and (member.id != ctx.author.id) and not _is_admin(ctx):
        await ctx.send("🚫 Only admins can view other members’ balances.")
        return
    data = _load_tokens()
    bal = _ensure_balance(data, target.id)
    who = target.mention if member else "You"
    await ctx.send(f"{who} have **{bal}** token(s).")

@bot.command(name="token")
async def token_admin_cmd(ctx, action: str = None, member: discord.Member = None, amount: int = None):
    """
    Admin token management.
    - ;token add @user <n>
    - ;token remove @user <n>
    - ;token set @user <n>     (optional convenience)
    """
    if not _is_admin(ctx):
        await ctx.send("🚫 You need **Manage Server** to modify tokens.")
        return

    valid_actions = {"add", "remove", "set"}
    if action is None or action.lower() not in valid_actions or member is None or amount is None:
        await ctx.send(
            "Usage:\n"
            "`;token add @user <n>`\n"
            "`;token remove @user <n>`\n"
            "`;token set @user <n>`"
        )
        return

    action = action.lower()
    try:
        n = int(amount)
    except Exception:
        await ctx.send("Amount must be an integer.")
        return

    data = _load_tokens()

    if action == "add":
        if n <= 0:
            await ctx.send("Add amount must be a positive integer.")
            return
        new_bal = _add_balance(data, member.id, n)
        await ctx.send(f"✅ Added **{n}** tokens to {member.mention}. New balance: **{new_bal}**.")

    elif action == "remove":
        if n <= 0:
            await ctx.send("Remove amount must be a positive integer.")
            return
        cur = _ensure_balance(data, member.id)
        new_bal = _set_balance(data, member.id, cur - n)
        removed = cur - new_bal
        await ctx.send(f"✅ Removed **{removed}** tokens from {member.mention}. New balance: **{new_bal}**.")

    elif action == "set":
        if n < 0:
            await ctx.send("Set amount must be zero or positive.")
            return
        new_bal = _set_balance(data, member.id, n)
        await ctx.send(f"✅ Set {member.mention}'s balance to **{new_bal}**.")

@bot.command(name="commands")
async def help_command(ctx):
    """Displays a list of all available commands and their descriptions."""
    help_text = (
        "**Available Commands:**\n\n"
        "**;species [species name]** – Shows an info card with image, taxonomy, and holdings for a species.\n"
        "**;type [type name]** – Lists all species belonging to a specific animal type (e.g. Mammal, Fish).\n"
        "**;order [order name]** – Lists species under a given taxonomic order.\n"
        "**;family [family name]** – Lists species belonging to a particular family.\n"
        "**;genus [genus name]** – Lists species belonging to a particular genus.\n"
        "**;region [region name]** – Lists species found in that region (e.g. Europe, Asia, North America).\n"
        "**;type all** – Lists all species in the database.\n"
        "**;addtokens [@user] [amount]** – Adds tokens to a user.\n"
        "**;removetokens [@user] [amount]** – Removes tokens from a user.\n"
        "**;tokens [@user]** – Shows how many tokens a user currently has.\n"
        "**;house [zoo name]** – Displays information about a specific zoo/institution.\n"
        "**;regionlist** – Lists all available regions.\n"
        "\n*(Use commands with care — names with multiple words should be in quotes!)*"
    )
    await ctx.send(help_text)

if __name__ == "__main__":
    # >>> ADDED: start keep-alive web server before running the bot <<<
    keep_alive()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN not set in environment or .env")
        sys.exit(1)
    bot.run(token)
