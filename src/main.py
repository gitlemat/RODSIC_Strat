import uvicorn
import logging
import os
from dotenv import load_dotenv
from logger import LoggerSetup

# Load Env
load_dotenv()
port = int(os.getenv("API_PORT", 8002))

# Setup Logging
logger = LoggerSetup.get_logger("Main")

if __name__ == "__main__":
    logger.info("Starting RODSIC Strategy Server API...")
    try:
        # Using string import to allow reloading
        uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
    except KeyboardInterrupt:
        logger.info("RODSIC Strategy Server stopped by user.")
