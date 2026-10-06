import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    for noisy in ("sqlalchemy.engine", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
