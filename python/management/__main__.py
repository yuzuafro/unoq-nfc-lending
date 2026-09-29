# Phase 2: run Management on its own, e.g. `python -m management` or via the Dockerfile.
import logging

from .app import serve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
serve()
