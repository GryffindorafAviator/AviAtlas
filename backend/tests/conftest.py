import pytest

from aviatlas.parsing import formats


@pytest.fixture(autouse=True)
def text_fixture_pdfs(request, monkeypatch):
    """Most tests upload plain text named `.pdf` and parse it with a fake parser.

    Skip only the PDF signature check for them; tests marked `real_formats`
    keep every content check."""
    if request.node.get_closest_marker("real_formats"):
        return
    original = formats.check_content

    def check(path, fmt):
        if fmt != "pdf":
            original(path, fmt)

    monkeypatch.setattr("aviatlas.services.documents.check_content", check)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_formats: keep upload content checks for PDFs")
