"""Placeholder ingest entrypoint (S1-T04) — replaced by the real pipeline in S1-T06+."""

from __future__ import annotations

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest")


def main() -> None:
    log.info("ingest placeholder: waiting for implementation (S1-T06+)")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
