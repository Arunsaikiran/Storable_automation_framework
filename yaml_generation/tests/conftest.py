"""Makes the framework importable for the YAML generation tests. Run from the repository root:

    python -m pytest yaml_generation/tests
"""

import sys
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))
