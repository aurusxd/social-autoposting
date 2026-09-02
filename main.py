import os

import uvicorn
from loguru import logger

from app.core.config import load_config, load_environment


def main() -> None:
    load_environment()
    config = load_config()
    host = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("WEB_PORT", "8000").strip() or "8000")
    logger.info(
        "Starting the control panel on {}:{} with {} static targets",
        host,
        port,
        len(config.targets),
    )
    uvicorn.run(
        "app.web.main:create_app",
        factory=True,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
 

if __name__ == "__main__":
    main()
