from streamlit.testing.v1 import AppTest


def test_app_runs_default():
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    assert not at.exception, at.exception
    assert len(at.metric) >= 8


def test_app_dry_year_and_karapinar():
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    at.selectbox[0].select_index(1)
    at.toggle[0].set_value(True)
    at.run()
    assert not at.exception, at.exception
