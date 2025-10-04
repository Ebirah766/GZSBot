# Overview

This is a Discord bot application built using discord.py that manages a virtual zoo and token economy system. The bot allows users to create and manage zoos, collect animals, and participate in a token-based economy. The application is designed to run on Replit's infrastructure with built-in web server functionality to maintain uptime.

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