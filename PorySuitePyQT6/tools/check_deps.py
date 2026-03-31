import importlib.util
import sys

REQS = [
    "PyQt6",
    "unidecode",
]

missing = [r for r in REQS if importlib.util.find_spec(r) is None]
if missing:
    print("Missing required Python packages: " + ", ".join(missing))
    print("Install with: python -m pip install -r requirements.txt")
    sys.exit(1)

sys.exit(0)

