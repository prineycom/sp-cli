import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sp_cli.ids import nanoid  # noqa: E402
from sp_cli.model import make_task  # noqa: E402
from sp_cli.queries import doctor  # noqa: E402
from sp_cli.webdav import strip_prefix  # noqa: E402

SAMPLE_PATH = REPO_ROOT / "docs" / "sample-sync-data.json"

_SAMPLE = json.loads(strip_prefix(SAMPLE_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def sample() -> dict:
    """Fresh deep copy of the real sample sync file (prefix stripped)."""
    return copy.deepcopy(_SAMPLE)


@pytest.fixture
def add_task_entity(sample):
    """Factory: insert a task entity directly into the state (no ops)."""

    def _add(d=None, **kwargs) -> dict:
        d = d if d is not None else sample
        state = d["state"]
        task_id = kwargs.pop("task_id", nanoid())
        project_id = kwargs.pop("project_id", "INBOX_PROJECT")
        title = kwargs.pop("title", "test task")
        task = make_task(task_id, title, project_id, **kwargs)
        state["task"]["ids"].append(task_id)
        state["task"]["entities"][task_id] = task
        if not task.get("parentId"):
            state["project"]["entities"][project_id]["taskIds"].append(task_id)
        return task

    return _add


def assert_doctor_clean(d: dict) -> None:
    problems = doctor(d)
    assert problems == [], f"invariant violations: {problems}"
