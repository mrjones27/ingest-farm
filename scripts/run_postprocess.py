import logging

from ingest_farm.postprocess.main import PostProcessWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    PostProcessWorker().run()


if __name__ == "__main__":
    main()
