from unittest.mock import AsyncMock

import pytest
from conftest import FakeActor, fixture_url
from src import main as main_mod
from src.main import Run, parse_input


@pytest.mark.parametrize("mode", ["page", "document", "chunk"])
async def test_exhausted_budget_never_downloads(mode, tmp_path, monkeypatch):
    actor = FakeActor(
        {"urls": [fixture_url("simple.pdf")], "outputMode": mode},
        prices={"page": 0.0003, "ocr-page": 0.003},
        max_total=0,
    )
    download = AsyncMock(side_effect=AssertionError("download must not start"))
    monkeypatch.setattr(main_mod, "download", download)
    run = Run(actor, parse_input(actor.input), memory_mb=512, work_dir=tmp_path)
    await run.process(None, fixture_url("simple.pdf"))
    download.assert_not_called()
    assert actor.rows("document")[0]["errorCode"] == "budget_exhausted"
    assert actor.cm.charged == {}
