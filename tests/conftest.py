import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.docker tests when Docker isn't reachable,
    rather than failing — most contributors won't have it set up."""
    from tests.docker_harness import docker_available

    if docker_available():
        return
    skip_docker = pytest.mark.skip(reason="Docker not available in this environment")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip_docker)
