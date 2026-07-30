"""Entry point for the Garage61 MCP server."""

import asyncio
import logging
import os
import sys

# The modules in this directory import each other by bare name (`from tools
# import ...`). That works when this file is run as a script, because Python
# puts its directory on sys.path -- but not under `python -m garage61_mcp` or
# via the console script. Adding it explicitly makes every entry point work.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402

from server import main as server_main  # noqa: E402


def _configure_logging() -> None:
    """Log to stderr; stdout is the MCP transport and must stay clean.

    Defaults to WARNING because DEBUG logs entire telemetry payloads.
    Override with GARAGE61_LOG_LEVEL=DEBUG when troubleshooting.
    """
    level_name = os.getenv("GARAGE61_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.WARNING),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )


def run():
    """Load environment and run the server."""
    _configure_logging()
    logger = logging.getLogger(__name__)

    load_dotenv()

    if not os.getenv("GARAGE61_TOKEN"):
        print(
            "Error: GARAGE61_TOKEN environment variable is required.\n"
            "Set it in the MCP server config, or create a .env file containing:\n"
            "GARAGE61_TOKEN=your-token-here",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info("Starting Garage61 MCP server")
    asyncio.run(server_main())


def main():
    """Main entry point for package."""
    run()


if __name__ == "__main__":
    main()
