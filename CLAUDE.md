# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A multi-DEX perpetual trading bot (다슬기 PERP DEX 봇) that enables simultaneous trading across multiple perpetual DEX platforms from a unified interface. Users can hedge positions, perform volume farming ("burn"), and manage assets across exchanges.

**Primary language**: Korean (comments, logs, error messages, documentation)

## Commands

```bash
# Install dependencies
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
pip install -r requirements.in

# Run application
python main.py              # Qt GUI (default)
python main.py --ui urwid   # Terminal UI

# Update fee rates from config.ini.example
python update_rates.py --dry-run  # Preview changes
python update_rates.py            # Apply changes

# Windows (PowerShell)
.\scripts\win\setup.ps1           # Initial setup
.\scripts\win\run.ps1             # Daily run
.\scripts\win\update-force.ps1    # Force update with backup
```

## Architecture

```
main.py                    Entry point, logging setup, UI selection
    ↓
ExchangeManager (core.py)  Loads config.ini, creates exchange clients via mpdex
    ↓
TradingService (trading_service.py)  Order execution, balance checks, collateral transfers
    ↓
UI Layer
├── ui_qt.py      Qt/PySide6 GUI (default, modern)
└── ui_urwid.py   Terminal TUI (legacy)
```

### Key Classes

- **ExchangeManager** (`core.py`): Manages all exchange instances. Loads `config.ini` sections, parses `initial_setup`, creates clients via `mpdex.exchange_factory.create_exchange()`. Key methods: `initialize_all()`, `visible_names()`, `first_hl_exchange()`, `is_hl_like()`

- **TradingService** (`trading_service.py`): Handles trading operations. Manages position orders, collateral transfers (Perp↔Spot), balance caching. Works with both Hyperliquid-based (via CCXT-like interface) and non-HL exchanges (via mpdex)

### Exchange Types

**Hyperliquid-based** (`hl=True`): lit, dexari, liquid, hyena, supercexy, basedone, dreamcash, mass, superstack, treadfi
- Use Agent API keys (not wallet private keys for trading)
- Support HIP-3 DEX selection: HL, XYZ, FLX, VNTL, HYNA
- Collateral: USDC, USDH (FLX/VNTL), USDE (HYNA)

**Non-Hyperliquid** (`hl=False`): lighter, paradex, edgex, grvt, backpack, variational, pacifica
- Each has unique API key structure (see `_build_mpdex_key()` in core.py)

### Configuration Files

- **`.env`**: API keys per exchange (format: `EXCHANGE_WALLET_ADDRESS`, `EXCHANGE_AGENT_API_KEY`, etc.)
- **`config.ini`**: Exchange visibility, fee rates, initial card setup
  - `show = True/False` - UI visibility
  - `exchange = hyperliquid|lighter|backpack|...` - Platform type
  - `builder_code` - HL builder address
  - `fee_rate = limit / market` - Fee in bps
  - `initial_setup = symbol, amount, side, type, group` - Card defaults

### Group System

Cards belong to groups 0-5. Each group has independent:
- Execute All / Reverse / Close All
- Repeat trading (interval-based)
- Burn mode (auto long↔short alternation)

Header controls apply to currently selected group only.

## Logging

Environment variables:
- `PDEX_LOG_LEVEL` (default: INFO)
- `PDEX_LOG_FILE` (default: debug.log)
- `PDEX_LOG_CONSOLE` (0|1)
- `PDEX_TS_LOG_FILE` (default: ts.log) - TradingService log
- `PDEX_LOG_SUPPRESS` (default: "asyncio,urllib3")

## Important Patterns

- All exchange clients are async; use `await manager.initialize_all()` before trading
- Fee rates support multiple formats: "20/25", "20, 25", "20|25", "20 25"
- Config encoding fallback: UTF-8 → UTF-8-SIG → CP949 → EUC-KR → MBCS
- DEX symbol format in `initial_setup`: `dex:SYMBOL` (e.g., `xyz:XYZ100`)
- Stablecoins: USDC, USDH, USDT0, USDE (varies by DEX)
