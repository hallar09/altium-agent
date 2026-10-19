# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""Allow running the package as a module: python -m altium_agent"""

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
