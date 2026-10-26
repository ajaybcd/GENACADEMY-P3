from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "app.py"


def test_demo_can_load_select_and_approve_without_live_credentials():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    next(b for b in app.button if b.label == "Explore demo").click().run()
    assert not app.exception
    assert app.metric[0].value == "3"
    # First finding, then explicit acknowledgement; no implicit approval.
    app.checkbox[0].check()
    app.checkbox[-1].check().run()
    next(b for b in app.button if b.label == "Approve demo selection").click().run()
    assert not app.exception
    assert any("No GitHub post" in s.value for s in app.success)


def test_live_mode_requires_model_credentials():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio[0].set_value("GitHub pull request").run()
    app.text_input[0].set_value("https://github.com/acme/project/pull/1")
    next(b for b in app.button if b.label == "Start review").click().run()
    assert not app.exception
    assert any("model API key" in e.value for e in app.error)
