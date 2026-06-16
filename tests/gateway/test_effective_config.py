from pathlib import Path


def test_gateway_effective_config_includes_default_mcp_servers(monkeypatch, tmp_path):
    from gateway import run as gateway_run
    from hermes_cli.tools_config import _get_platform_tools

    config_path = tmp_path / "config.yaml"
    raw_config = {
        "platform_toolsets": {
            "slack": ["terminal"],
        },
    }
    effective_config = {
        **raw_config,
        "mcp_servers": {
            "odoo-hedge-server": {
                "url": "http://192.168.139.7:18079/mcp",
            },
        },
    }

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr("hermes_cli.config.get_config_path", lambda: config_path)
    monkeypatch.setattr("hermes_cli.config.read_raw_config", lambda: raw_config)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: effective_config)

    assert "mcp_servers" not in gateway_run._load_gateway_config()
    toolsets = _get_platform_tools(gateway_run._load_gateway_effective_config(), "slack")

    assert "odoo-hedge-server" in toolsets


def test_gateway_effective_config_falls_back_to_raw_for_noncanonical_home(
    monkeypatch,
    tmp_path,
):
    from gateway import run as gateway_run

    config_path = tmp_path / "config.yaml"
    raw_config = {"model": {"default": "raw-model"}}

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        "hermes_cli.config.get_config_path",
        lambda: Path("/elsewhere/config.yaml"),
    )
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"model": {"default": "merged-model"}},
    )
    config_path.write_text("model:\n  default: raw-model\n", encoding="utf-8")

    assert gateway_run._load_gateway_effective_config() == raw_config
