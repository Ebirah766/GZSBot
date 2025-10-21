# Overview

This is a Discord bot application built using discord.py that manages a virtual zoo and token economy system. The bot allows users to create and manage zoos, collect animals, and participate in a token-based economy. The application is designed to run on Replit's infrastructure with built-in web server functionality to maintain uptime.

# Recent Changes

**2025-10-21**: Added missing zoos to directory in zoo_progress.json and fixed Wild Boar syntax errors:
- Added "Species Watch" to zoo directory (zoo was in ownership data but missing from directory)
- Added "Giardino Zoologico e Botanico La Sapienza" to zoo directory (zoo was in species data but missing from directory)
- Wild Boar species data: Fixed multiple syntax errors:
  - Line 345-350: Added missing closing bracket `]` for images array
  - Line 362: Added missing comma after "Cube Zoological Park": "1.1" in institutions dictionary
- Bot now running successfully with 17 zoos recognized in directory: Air Terjun Zoo, Credit River Zoo, Cube Zoological Park, Essex County Zoo, Giardino Zoologico e Botanico La Sapienza, Glacier Zoo, High Uintahs Zoo, Jupiter Reptile Zoo, Kings of the Jungle, Mint Park Zoo, New York Aquarium, North Star Zoo, Sapporo Reptile Center and National Aquarium, Shropshire Hills Zoo, Species Watch, Wasser Wunder Welt, Wildkatzenpark Tatzenfels

**2025-10-20**: Fixed recurring missing comma syntax errors in species data:
- Axolotl species data (line 1961): Added missing comma after "Kings of the Jungle": "2.2" in institutions dictionary (fixed multiple times - file keeps reverting)
- Common Carp species data (line 2292): Added missing comma after "New York Aquarium": "10" in institutions dictionary
- Common Snapping Turtle species data (line 3804): Added missing comma after "Essex County Zoo": "1.0" in institutions dictionary
- Pumpkinseed species data (line 4557): Added missing comma after "Cube Zoological Park": "3" in institutions dictionary (fixed multiple times - file keeps reverting)
- Bot successfully restarted and running without errors
- **Important Note**: These same errors have recurred multiple times within this session, strongly suggesting the bot.py file is being edited externally, which reverts fixes. Pattern: Missing commas between institution entries in species data dictionaries

**2025-10-19**: Fixed syntax errors in species data and error handler:
- American Alligator species data (line 1986): Added missing comma after "Essex County Zoo": "1.1" in institutions dictionary
- American Bullfrog species data (line 3900): Added missing comma after "Essex County Zoo": "1.0" in institutions dictionary
- Line 7118: Changed `CommandNotFound` to `commands.CommandNotFound` to fix NameError that was occurring when invalid commands were sent
- Bot now properly handles invalid commands without errors

**2025-10-18**: Fixed recurring orphaned code issue in build_species_embed function and Bobcat species data:
- Root cause: The `build_species_embed` function (line 5367) was incomplete - missing its final sections (info field, holdings processing, images handling, and return statement)
- This code was appearing as "orphaned code" at module level (lines 5382-5416) with incorrect indentation, referencing variables like `entry` and `e` that don't exist at module level
- Solution: Moved `_format_region_holdings` helper function to module level (before build_species_embed, line 5336), then completed build_species_embed with all necessary code inside function body with correct indentation
- Fixed structure: Helper function at module level → complete build_species_embed function with info field, holdings iteration using helper, images pager logic, and return statement all properly indented inside function
- Bobcat species data (line 1688): Added missing comma after "Cube Zoological Park": "1.0" in institutions dictionary
- Previous fixes (same session): Japanese Rhinoceros Beetle truncated info field, Camoflauge Isopod invalid comma, multiple indentation errors
- Added "Kings of the Jungle" to zoo directory in zoo_progress.json
- Bot now running successfully with 15 zoos recognized in directory: Air Terjun Zoo, Credit River Zoo, Cube Zoological Park, Essex County Zoo, Glacier Zoo, High Uintahs Zoo, Jupiter Reptile Zoo, Kings of the Jungle, Mint Park Zoo, New York Aquarium, North Star Zoo, Sapporo Reptile Center and National Aquarium, Shropshire Hills Zoo, Wasser Wunder Welt, Wildkatzenpark Tatzenfels

**2025-10-17**: Fixed indentation, syntax, runtime errors, and typos in bot.py:
- Fixed typo: Replaced all instances of "Invertberate" with "Invertebrate" throughout species data
- Fixed NameError in error handler: Changed `CommandNotFound` to `commands.CommandNotFound` (line 6613)
- Lines 4412-4536: De-indented helper functions (_norm_zoo, _build_canonical_zoo_map, etc.) to module level - they were over-indented by 4 spaces
- Lines 4449-4475: Fixed indentation for _extract_species_set and _extract_directory_species functions
- Lines 4501-4538: Fixed indentation for _get_held_for_zoo function
- Line 4494: Fixed malformed parameter name (_get_held_for_zooowner_uid → owner_uid)
- Removed duplicate over-indented _extract_species_set and _extract_directory_species functions at lines 4562-4588 that were causing NameError
- Line 5069: Fixed extra space before async in cmd_progress function definition
- Fixed cmd_progress function body indentation (reduced from 12 to 4 spaces)
- Functions now properly recognized by Python interpreter
- Fixed invalid semicolon syntax error in bot.py:
- Line 4948: Split `got = walk(it); if got: return got` into two proper lines
- Python does not allow semicolons to separate statements on the same line in this context
- Fixed mismatched brackets in Titan Stag Beetle species data (line 4137-4143): Added missing closing bracket `]` for images array
- Fixed malformed dictionary keys in species data (lines 4161, 4186, 4213, 4238, 4263): Changed `Invertebrate "Invertberate",` to `"type": "Invertberate",` - missing `"type":` prefix in Japanese Rhinoceros Beetle, Metallic Stag Beetle, Godzilla Isopod, Javan Leaf Insect, and Camoflauge Isopod entries
- Fixed truncated "info" fields in species data: Japanese Rhinoceros Beetle (line 4160) and Godzilla Isopod (line 4212) - text was cut off mid-sentence and missing closing quotes
- Bot now runs successfully without syntax errors

**2025-10-09**: Fixed recurring syntax errors in species_data:
- Common Fallow Deer: Added missing commas in holdings (line 962) and institutions (line 969) dictionaries
- Cotton-Top Tamarin: Added missing comma between institution entries (line 1315)
- Turkey Vulture: Added missing comma between institution entries (line 2122)
- Added "Air Terjun Zoo" to zoo directory in zoo_progress.json
- Note: Common Fallow Deer syntax error has recurred multiple times - always check for missing commas when adding Air Terjun Zoo data

**2025-10-06**: Fixed critical indentation errors in bot.py:
- Corrected SpeciesPager class indentation (was nested inside function, moved to top level)
- Removed duplicate build_species_embed function definition that was missing total_images parameter
- Fixed token_admin_cmd function indentation (reduced from 16 to 8 spaces)
- Bot now compiles successfully and runs without syntax errors

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Application Structure

**Problem**: Need a maintainable Discord bot with clear separation of concerns.

**Solution**: Modular architecture with separate files for core bot logic (`bot.py`), web server functionality (`keep_alive.py`), and entry point (`main.py`).

**Rationale**: This separation allows independent development and testing of components. The Flask server runs in a separate thread to keep the bot alive on Replit's infrastructure.

## Bot Framework

**Problem**: Need to handle Discord events and commands efficiently.

**Solution**: Built on discord.py 2.3.2 using the commands extension with prefix-based commands (`;` prefix).

**Key Decisions**:
- Message content intent enabled to read user messages
- Command prefix pattern for traditional Discord bot interaction
- Comprehensive logging to both file and stdout for debugging

## Data Persistence

**Problem**: Need to persist user data for zoo collections and token balances.

**Solution**: File-based JSON storage with two separate data files:
- `tokens.json`: Stores user token balances keyed by Discord user ID
- `zoo_progress.json`: Stores zoo ownership, animal collections, and user limits

**Pros**: 
- Simple implementation without database setup
- Human-readable format for debugging
- No external dependencies

**Cons**:
- Not suitable for high-traffic scenarios
- No transaction safety
- Potential data loss on concurrent writes

**Note**: This approach works for small-scale Discord bots but should be migrated to a proper database (e.g., PostgreSQL with an ORM) for production use.

## Uptime Management

**Problem**: Replit requires active HTTP endpoints to maintain container uptime.

**Solution**: Flask web server running on port 8080 in a daemon thread, responding to health checks.

**Implementation**: The `keep_alive()` function starts a background Flask server that responds with "OK" to GET requests on the root endpoint. This satisfies Replit's uptime ping requirements.

## Error Handling and Logging

**Problem**: Need visibility into bot operations and errors.

**Solution**: Python's logging module configured to output to both `bot.log` and stdout with timestamp, level, and logger name formatting.

**Key Features**:
- CommandNotFound errors can be gracefully handled
- Environment variable validation logged at startup
- Safe fallback for optional dotenv loading

## Configuration Management

**Problem**: Need secure token management and environment-specific configuration.

**Solution**: Environment variables via `python-dotenv` with graceful degradation if not available.

**Key Variables**:
- `DISCORD_TOKEN`: Bot authentication token

# External Dependencies

## Discord API

**Service**: Discord Bot API via discord.py library (v2.3.2)

**Purpose**: Core bot functionality including message handling, command processing, and Discord event management.

**Integration**: Uses Discord's Gateway API with WebSocket connections for real-time event streaming.

## Replit Infrastructure

**Service**: Replit hosting platform

**Purpose**: Application hosting and container management.

**Requirements**: HTTP server on port 8080 (0.0.0.0) to maintain uptime.

## Flask Web Framework

**Service**: Flask 3.0.0

**Purpose**: Minimal HTTP server for Replit uptime checks.

**Integration**: Runs in background thread; does not interfere with Discord bot operations.

## Python-dotenv

**Service**: python-dotenv 1.0.1 (optional)

**Purpose**: Environment variable management for local development.

**Integration**: Safe import with fallback handling if not installed.