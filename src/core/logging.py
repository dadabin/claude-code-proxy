import json
import logging
from typing import Any, Dict

from src.core.config import config

log_level = config.log_level.split()[0].upper()
valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
if log_level not in valid_levels:
    log_level = "INFO"

logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

for uvicorn_logger in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
    logging.getLogger(uvicorn_logger).setLevel(logging.WARNING)


def emit_request_audit_log(event: str, payload: Dict[str, Any]) -> None:
    audit_payload = {"event": event, **payload}
    logger.info("request_audit %s", json.dumps(audit_payload, ensure_ascii=False, default=str))
