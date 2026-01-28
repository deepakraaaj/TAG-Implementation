import logging
from ..config import get_settings

def setup_logging():
    logging.basicConfig(level=get_settings().LOG_LEVEL)
    logging.getLogger("uvicorn.access").disabled = False
