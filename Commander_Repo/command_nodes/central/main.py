"""main.py
CentralNode process entrypoint
Usage:
python -m command_nodes.central.main
ENV=docker python -m command_nodes.central.main
"""

from __future__ import annotations

# Standard Library

import time

# Project Imports

from command_nodes.central.services.node_runtime import CentralNode

if __name__ == "__main__":
    node = CentralNode()
    node.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.stop()
