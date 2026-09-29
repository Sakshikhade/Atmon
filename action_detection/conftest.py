"""Put the repo root on sys.path for every test, once.

Without this, `import src...` / `import webapp.server` only resolves when pytest
happens to be invoked from the repo root, because each test module was doing its
own sys.path.insert before its imports. pytest imports conftest.py before
collecting anything, so this runs first regardless of the working directory.

Kept at the repo root rather than in tests/ so `pytest tests/test_x.py` from any
directory works -- pytest walks up from the target looking for conftest files.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
