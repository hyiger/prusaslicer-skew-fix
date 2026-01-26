import importlib.util
from pathlib import Path
import sys
import pytest

@pytest.fixture()
def load_module():
    mod_path = Path(__file__).resolve().parents[1] / "skew_fix_ps.py"
    spec = importlib.util.spec_from_file_location("skew_fix_ps", mod_path)
    module = importlib.util.module_from_spec(spec)
    # Ensure the module is present in sys.modules during execution (needed for dataclasses + string annotations)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
