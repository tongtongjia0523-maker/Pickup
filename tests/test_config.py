from ai_memory.config import DEFAULT_CONFIG, load_config, write_default_config


def test_defaults_without_file(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg["provider"]["name"] == "none"
    assert cfg["retention"]["enabled"] is False
    assert cfg["trigger"]["catch_up"] is False


def test_file_overrides_section(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[provider]\nname = "openai"\nbase_url = "http://localhost:1234/v1"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg["provider"]["name"] == "openai"
    assert cfg["provider"]["base_url"] == "http://localhost:1234/v1"
    # 未写的键保留默认值
    assert cfg["provider"]["timeout_seconds"] == 30
    assert cfg["retention"]["enabled"] is False


def test_env_provider_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text('[provider]\nname = "none"\n', encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_PROVIDER", "anthropic")
    cfg = load_config(p)
    assert cfg["provider"]["name"] == "anthropic"


def test_config_path_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(tmp_path / "custom.toml"))
    from ai_memory.config import config_path
    assert config_path() == tmp_path / "custom.toml"


def test_write_default_config_parseable(tmp_path):
    p = write_default_config(tmp_path / "config.toml")
    assert p.exists()
    cfg = load_config(p)  # 默认配置文件必须能被解析且等价于内置默认
    assert cfg["provider"]["name"] == DEFAULT_CONFIG["provider"]["name"]


def test_write_default_config_not_overwrite(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[provider]\nname = "anthropic"\n', encoding="utf-8")
    write_default_config(p)
    assert "anthropic" in p.read_text(encoding="utf-8")
