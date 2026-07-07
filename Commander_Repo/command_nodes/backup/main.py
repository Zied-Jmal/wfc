"""main.py
BackupCommander process entrypoint
Usage:
python -m command_nodes.backup.main
ENV=docker python -m command_nodes.backup.main
"""

from __future__ import annotations

# Standard Library

import time

# Project Imports

from command_nodes.backup.services.backup_commander import BackupCommander

if __name__ == "__main__":
    node = BackupCommander()
    node.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.stop()
