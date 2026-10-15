#!/usr/bin/env python3
"""Hermes scheduled entry point: nudge when CTI briefs wait for wiki review. Empty stdout = no message."""
import runpy
import sys
from pathlib import Path

sys.argv = ["briefs.py", "remind"]
runpy.run_path(str(Path.home() / '.hermes/skills/research/cti-brief-to-wiki/scripts/briefs.py'), run_name='__main__')
