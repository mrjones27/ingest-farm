import logging

import uvicorn

from ingest_farm.api.main import create_app
from ingest_farm.config import get_settings
from ingest_farm.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    settings = get_settings()
    init_db()
    app = create_app()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
