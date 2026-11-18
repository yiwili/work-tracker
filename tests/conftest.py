import pytest


@pytest.fixture(autouse=True)
def isolate_awt_data_dir(tmp_path, monkeypatch):
    """Keep credentials and model status writes inside the test's temp directory."""
    data = tmp_path / 'awt-data'
    monkeypatch.setenv('AWT_DATA_DIR', str(data))
    return data
