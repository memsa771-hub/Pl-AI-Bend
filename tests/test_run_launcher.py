import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("pai_launcher", Path(__file__).parents[1] / "run.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.mark.parametrize("service", ["intelligence", "goals", "documents"])
def test_worker_interrupted_during_import_exits_quietly(monkeypatch, capsys, service):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["run.py"])
    monkeypatch.setattr(launcher.runpy, "run_module", interrupted)
    assert launcher.run_service(service) == 0
    assert capsys.readouterr().err == ""


def test_api_interrupt_exits_quietly(monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=interrupted))
    assert launcher.run_service("api") == 0


def test_real_startup_errors_are_not_hidden(monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(sys, "argv", ["run.py"])
    monkeypatch.setattr(launcher.runpy, "run_module", failed)
    with pytest.raises(RuntimeError, match="startup failed"):
        launcher.run_service("goals")
