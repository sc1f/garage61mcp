"""Entry point for the Garage61 MCP server."""

import asyncio
import os
import logging
from dotenv import load_dotenv
from .server import main

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run():
    """Load environment and run the server."""
    logger.info("Starting Garage61 MCP server entry point")
    
    # Load environment variables from .env file
    load_dotenv()
    logger.debug("Environment variables loaded")
    
    # Check for required environment variables
    token = os.getenv("GARAGE61_TOKEN")
    if not token:
        logger.error("GARAGE61_TOKEN environment variable not found")
        print("Error: GARAGE61_TOKEN environment variable is required")
        print("Please create a .env file with your Garage61 API token:")
        print("GARAGE61_TOKEN=your-token-here")
        exit(1)
    
    logger.info(f"Token found (length: {len(token)})")
    
    # Run the server
    logger.info("Starting main server loop")
    asyncio.run(main())


if __name__ == "__main__":
    run()