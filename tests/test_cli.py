import sys

from nukefm import __main__


def test_serve_enables_local_proxy_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_load_settings():
        return type("Settings", (), {"log_path": "logs/test.log"})()

    def fake_configure_logging(log_path) -> None:
        captured["log_path"] = log_path

    def fake_run(app, **kwargs) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(__main__, "load_settings", fake_load_settings)
    monkeypatch.setattr(__main__, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(__main__.uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["nukefm", "serve", "--host", "127.0.0.1", "--port", "8000"])

    __main__.main()

    assert captured["log_path"] == "logs/test.log"
    assert captured["app"] == "nukefm.app:create_app"
    assert captured["kwargs"] == {
        "factory": True,
        "host": "127.0.0.1",
        "port": 8000,
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1",
    }
