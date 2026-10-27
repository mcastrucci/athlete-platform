"""Worker bootstrap. Durable queue handlers are implemented in issue #6."""

import logging

logger = logging.getLogger(__name__)


def main() -> int:
    """Exit without polling or opening connections until job handlers exist."""
    logging.basicConfig(level=logging.INFO)
    logger.info("No job handlers are configured; exiting without polling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
