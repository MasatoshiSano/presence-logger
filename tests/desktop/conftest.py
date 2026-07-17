import sys
from pathlib import Path

# The tool package lives under a hyphenated dir (not a dotted-importable path),
# so put its parent on sys.path to allow `import pipeline_monitor`.
_TOOLS = Path(__file__).resolve().parents[2] / "desktop" / "presence-tools"
sys.path.insert(0, str(_TOOLS))
