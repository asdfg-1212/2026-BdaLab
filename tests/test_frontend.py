from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_landing_page_has_chat_and_no_exception():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "frontend/app.py"))
    app.run(timeout=20)
    assert not app.exception
    assert app.title[0].value == "MovieLens 数据治理 Agent"
    assert len(app.chat_input) == 1
    assert not app.chat_input[0].disabled
