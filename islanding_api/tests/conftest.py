import os
import sys

# Every module under islanding_api/ uses bare imports ("from models import
# GridState", "from database import get_db") rather than package-relative
# ones, so tests need the app directory on sys.path regardless of the
# directory pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture
def no_trained_model(monkeypatch):
    """Forces classify_grid_state() onto the rule-based fallback path,
    regardless of whether models/rf_grid_state.joblib happens to exist on
    whatever machine the suite runs on - fallback-threshold tests should be
    deterministic, not dependent on local filesystem state."""
    import decision_layer
    monkeypatch.setattr(decision_layer, "_rf_bundle", None)
    monkeypatch.setattr(decision_layer, "_rf_load_attempted", True)
