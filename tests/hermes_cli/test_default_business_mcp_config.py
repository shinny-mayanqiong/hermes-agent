from hermes_cli.config import DEFAULT_CONFIG


def test_default_config_includes_evaluation_control_workflow():
    server = DEFAULT_CONFIG["mcp_servers"]["hedge-evaluation-control"]

    assert server == {
        "url": "http://192.168.139.7:28083/mcp",
        "timeout": 120,
        "connect_timeout": 60,
    }
    assert "hedge-evaluation-deploy" in DEFAULT_CONFIG["plugins"]["enabled"]
