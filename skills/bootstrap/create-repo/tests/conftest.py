"""Put this ``tests/`` directory on ``sys.path`` for the whole subtree.

The ``e2e/`` suites reuse the repo-builder fixtures defined in their sibling
in-process test modules (e.g. ``test_check_repo_baseline``); pytest's prepend
import mode only adds each test file's own directory, so without this the
``e2e/`` files could not import ``test_check_repo_baseline``. Loading a conftest
here makes ``tests/`` importable across both tiers, keeping the builders defined
once instead of duplicated into the e2e files.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
