import pytest


@pytest.fixture
def video_path(tmp_path):
    """Provide a placeholder video path for the manual language guard test."""
    return str(tmp_path / "dummy_video.mkv")
