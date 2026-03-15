"""Pytest configuration and fixtures for auto-review-fixer tests."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Add src to path so tests can import auto_fixer, state_manager, summarizer, etc.
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


@pytest.fixture
def make_cmd_result():
    """subprocess 結果モックのファクトリ。

    Usage:
        result = make_cmd_result('[{"number": 1}]')
        result = make_cmd_result("error msg", returncode=1, stderr="err")
    """

    def _make(stdout="", *, returncode=0, stderr=""):
        return Mock(returncode=returncode, stdout=stdout, stderr=stderr)

    return _make


@pytest.fixture
def make_process_mock():
    """subprocess.Popen モックのファクトリ（claude CLI 呼び出し向け）。

    Usage:
        process = make_process_mock('{"result": "ok"}')
        process = make_process_mock(stdout="out", stderr="err", returncode=1)
    """

    def _make(stdout="", stderr="", returncode=0):
        process = Mock()
        process.communicate.return_value = (stdout, stderr)
        process.returncode = returncode
        return process

    return _make
