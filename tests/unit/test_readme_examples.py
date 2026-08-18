from pathlib import Path

from tests.examples import CREATE_REQUEST, as_json

README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_shows_the_same_request_as_the_tests() -> None:
    """§9 CLAUDE.md: примеры берутся из приёмочных тестов. Тест — то место,
    где расхождение становится видно сразу."""
    assert as_json(CREATE_REQUEST) in README.read_text(encoding="utf-8")
