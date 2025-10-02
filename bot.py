# bot.py
import os
import sys
import logging
import pathlib
import difflib
import re
from typing import Dict, Any, Tuple, Optional, List

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

# --- Species DB --------------------------------------------------------------
REGIONS = ["North America", "Europe", "Asia", "Africa", "South America", "Oceania"]

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
        "holdings": {
            "North America": "Cube Zoological Park (2)",
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
        "holdings": {
            "North America": "Cube Zoological Park (2.4)",
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
        "holdings": {
            "North America": "Cube Zoological Park (100)",
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
        "holdings": {
            "North America": "Cube Zoological Park (1.1)",
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
        "holdings": {
            "North America": "Cube Zoological Park (1.0)",
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
            "North America": "Cube Zoological Park (2.4)",
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
        "holdings": {
            "North America": "Cube Zoological Park (5)",
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
        "holdings": {
            "North America": "Cube Zoological Park (0.1)",
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
        "holdings": {
            "North America": "Cube Zoological Park (1.1)",
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
    "Eastern Diamondback Rattlesnake": {
        "common": "Eastern Diamondback Rattlesnake",
        "scientific": "Crotalus adamanteus",
        "info": "The eastern diamondback rattlesnake is the largest rattlesnake species. It is endemic to the southeastern United States, and is one of the heaviest species of venomous snakes.",
        "type": "Reptile",
        "order": "Squamata",
        "family": "Viperidae",
        "genus": "Crotalus",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Adult_Crotalus_adamanteus.jpg/1280px-Adult_Crotalus_adamanteus.jpg",
        "holdings": {
            "North America": "Cube Zoological Park (1.0)",
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
            {"label": "South African cheetah", "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/92/Male_cheetah_facing_left_in_South_Africa.jpg/1280px-Male_cheetah_facing_left_in_South_Africa.jpg"},
            {"label": "Variant 2 caption", "url": "https://example.com/variant2.jpg"},
            {"label": "Variant 3 caption", "url": "https://example.com/variant3.jpg"},
        ],
        "image_url": "https://example.com/default.jpg",
        "holdings": {
            "North America": 0,
            "Europe": ["0.0.1.0 (South African) - Shropshire Hills Zoo"],
            "Asia": 0,
            "Africa": 0,
            "South America": 0,
            "Oceania": 0,
        },
        "institutions": {
            "Shropshire Hills Zoo": "0.0.1.0 [South African]"
        },
    },
    "Domestic Horse": {
        "common": "Domestic Horse",
        "scientific": "Equus ferus caballus",
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
}

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
    for region in REGIONS:
        value = holdings.get(region, None)
        if value is None or value == "":
            # treat as 0 / skip to explicit zero
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
            # Normalize singular values
            # If numeric zero or string "0" -> show zero inline
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
    if entry.get("info"):
        e.add_field(name="About", value=entry["info"], inline=False)
    holdings = entry.get("holdings", {})
    e.add_field(name="Holdings by Region", value=format_holdings(holdings), inline=False)

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

# --- Commands ----------------------------------------------------------------
@bot.command(name="card", aliases=["species"])
async def cmd_card(ctx: commands.Context, *, name: str):
    """
    Render a rich embed UI card for a species with image, taxonomy, description,
    and holdings by region.
    """
    try:
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
        log.exception("Error in ;card")
        await ctx.send(f"Sorry, something went wrong building the card for **{name}**.")

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
        entry, msg = get_entry_or_message(name)
        if not msg:
            value = entry.get("type")
            if value:
                await ctx.send(f"**{entry['common']}** is a **{value}**.")
            else:
                await ctx.send(f"No type information stored for **{entry['common']}**.")
            return
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

if __name__ == "__main__":
    # >>> ADDED: start keep-alive web server before running the bot <<<
    keep_alive()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN not set in environment or .env")
        sys.exit(1)
    bot.run(token)
