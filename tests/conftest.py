import sys
from pathlib import Path
import importlib
import pytest

# Ensure repository root is on sys.path for test imports
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

@pytest.fixture
def load_module():
    """Load skew_fix_ps fresh for tests that expect this fixture."""
    if 'skew_fix_ps' in sys.modules:
        del sys.modules['skew_fix_ps']
    if 'gcode_lib' in sys.modules:
        del sys.modules['gcode_lib']
    return importlib.import_module('skew_fix_ps')
