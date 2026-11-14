"""Where the clients live: their CNTRL names (clients/cntrl/cntrl, ...), or in a
checkout from before the move their old ones. The server resolves them the
same way (backend/devices_api.py)."""
from backend.devices_api import BROWSER_EXT, CLI_DIR, DESK_CLIENT, _cli_file

CLI = _cli_file()                       # the terminal client, one file
CLI_INSTALL = CLI_DIR / "install.sh"
DESK = DESK_CLIENT                      # the computer-use client, one file
BROWSER = BROWSER_EXT                   # the browser extension's folder

__all__ = ["CLI", "CLI_INSTALL", "DESK", "BROWSER"]
