#!/usr/bin/env python3
"""
Entry point wrapper for Heaven Cloud / Pterodactyl panel.
Executes malda_bot.main() via asyncio.run().
"""
from __future__ import annotations

import asyncio
import sys

from malda_bot import logger, main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
