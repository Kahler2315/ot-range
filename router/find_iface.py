"""Print the name of the network interface holding an address inside the
given subnet. Used by entrypoint.sh instead of assuming eth0 — Docker
Compose does not guarantee interface naming matches network declaration
order.
"""

from __future__ import annotations

import ipaddress
import subprocess  # nosec B404 — literal args only, see the call site below
import sys


def main() -> int:
    subnet = ipaddress.ip_network(sys.argv[1])
    # "ip" resolves via PATH deliberately (matches how it'd resolve for
    # anyone running this by hand in the container); literal argument
    # list, no shell, no external input.
    out = subprocess.run(  # nosec B603 B607
        ["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, check=True
    ).stdout
    for line in out.splitlines():
        fields = line.split()
        iface, cidr = fields[1], fields[3]
        addr = ipaddress.ip_address(cidr.split("/")[0])
        if addr in subnet:
            print(iface)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
