import os
import sys

# Repo root on path for imports from service modules
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SHARED_CONFIG = os.path.join(ROOT, "shared_config")
if SHARED_CONFIG not in sys.path:
    sys.path.insert(0, SHARED_CONFIG)
