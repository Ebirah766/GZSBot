# Overview

This project is a Discord bot designed to manage a virtual zoo and token-based economy. It allows users to create zoos, collect animals, and participate in a virtual currency system. The bot is built with `discord.py` and is hosted on Replit, utilizing its web server capabilities to ensure continuous uptime. The long-term vision is to provide an engaging and persistent virtual pet experience within Discord.

# Recent Changes

**2025-11-19 (Latest)**: Fixed recurring SSH sync corruption (5th incident):
- SSH sync from local Windows machine repeatedly corrupted entire bot.py file (12,369 lines)
- All lines were indented with 4 extra spaces, causing IndentationError at line 2
- Fixed by removing 4 leading spaces from all lines using Python script
- Added missing imports: `from datetime import datetime` and `from zoneinfo import ZoneInfo`
- Bot successfully restarted with all 326 species
- **CRITICAL ISSUE**: Bidirectional SSH sync continuously overwrites Replit fixes with broken local file
- **Solution implemented**: User instructed to disconnect SSH client and work directly on Replit until local file is properly synchronized

**2025-11-19 (Earlier)**: Fixed indentation errors and duplicate event handlers:
- Removed stray `return by_guild` line at line 11921 that caused IndentationError
- Fixed duplicate `on_ready()` event handlers (had two defined at lines 11408 and 12211)
- Merged both on_ready handlers into one, moving scheduled task starts (weekly_breeding and progress_role_sweeper) into the main on_ready event
- Fixed incorrectly indented `@tasks.loop` decorator at line 11996 (weekly_breeding task had 16 spaces instead of module-level indentation)
- Bot successfully restarted with all 326 species

**2025-11-13**: Fixed multiple syntax errors from SSH sync issues:
- Fixed Bee Shrimp entry (line ~2614): Missing closing braces for institutions and species objects
- Fixed Bubble-Tip Anemone entry: Incorrect indentation and extra closing braces
- Fixed Giant Green Anemone entry: Misaligned closing braces
- Bot successfully restarted with all 326 species
- **Known issue**: SSH sync from local Windows machine (C:\Users\sperm\Desktop\gzsbot) keeps overwriting Replit fixes with broken local file
- Recommended solution: Use Git-based workflow instead of direct SSH sync to prevent overwrite issues

**2025-11-12**: Database expanded to 326 species:
- Latest additions include: Siamese Spitting Cobra, Brown-Banded Cobra, Omkoi Lance-Headed Pit Viper, Phuket Pit Viper, Lanna Green Pit Viper, Guo's Green Pit Viper, Cryptic Green Pit Viper, African Fat-Tailed Gecko, San Francisco Brine Shrimp, American Tadpole Shrimp, Carolina Sphinx Moth, Argentine Horned Frog, Ball Python, Banggai Cardinalfish, Percula Clownfish
- Species breakdown: 79 Invertebrates, 76 Mammals, 64 Fish, 51 Reptiles, 39 Birds, 11 Amphibians

**2025-11-11**: Added new zoo "Chiang Mai Serpentarium" to zoo directory:
- Discovered zoo was referenced in species data but missing from zoo_progress.json directory
- Added "Chiang Mai Serpentarium" entry to directory in zoo_progress.json
- Zoo directory expanded from 17 to 18 zoos
- Bot successfully restarted and recognizes new zoo

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Application Structure

The application employs a modular architecture, separating core bot logic (`bot.py`), web server functionality (`keep_alive.py`), and the main entry point (`main.py`) for maintainability and independent component development.

## Bot Framework

The bot is built using `discord.py` version 2.3.2, leveraging its commands extension with a `;` prefix for user interactions. It utilizes Discord's message content intent for processing commands and incorporates comprehensive logging for debugging.

## Data Persistence

User data, including token balances (`tokens.json`) and zoo progress (`zoo_progress.json`), is stored using file-based JSON. This approach offers simplicity and human-readable data, suitable for small-scale operations.

## Uptime Management

To meet Replit's uptime requirements, a Flask web server runs on port 8080 in a daemon thread, responding to health checks and ensuring the bot remains active.

## Error Handling and Logging

The bot uses Python's `logging` module to output operational information and errors to both `bot.log` and standard output, with detailed formatting. It includes graceful handling for `CommandNotFound` errors.

## Configuration Management

Environment variables, managed via `python-dotenv` (optional), are used for secure configuration, primarily for the `DISCORD_TOKEN`.

# External Dependencies

## Discord API

The project integrates with the Discord Bot API through the `discord.py` library (v2.3.2) for all core bot functionalities, including command processing and event management via WebSocket connections.

## Replit Infrastructure

Replit serves as the hosting platform, requiring an active HTTP server on port 8080 for application uptime and container management.

## Flask Web Framework

Flask 3.0.0 is used to implement a minimal HTTP server that runs in a background thread to satisfy Replit's uptime monitoring requirements.

## Python-dotenv

The `python-dotenv` library (1.0.1) is optionally used for managing environment variables during local development.