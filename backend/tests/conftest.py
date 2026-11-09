"""Make the backend package importable when running pytest from backend/.

pytest inserts the test file's directory into sys.path, not the backend
directory, so `import app...` would fail without this.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
