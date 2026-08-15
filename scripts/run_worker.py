import logging

from ingest_farm.worker.main import IngestWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    IngestWorker().run()


if __name__ == "__main__":
    main()
