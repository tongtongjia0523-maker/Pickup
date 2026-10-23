from ai_memory.config import (DEFAULT_CONFIG, load_config, save_config_values,
                              write_default_config)


def test_defaults_without_file(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg["provider"]["name"] == "auto"   # 默认自动接入可用模型
    assert cfg["provider"]["auto_detect"] is True
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


# ---------- v3：配置写回（web 配置页用） ----------

def test_save_config_values_updates_and_keeps_comments(tmp_path):
    p = tmp_path / "config.toml"
    write_default_config(p)
    save_config_values({"provider": {"name": "openai", "timeout_seconds": 45}}, p)
    text = p.read_text(encoding="utf-8")
    assert "# auto" in text              # 注释模板仍在
    cfg = load_config(p)
    assert cfg["provider"]["name"] == "openai"
    assert cfg["provider"]["timeout_seconds"] == 45
    assert cfg["trigger"]["catch_up"] is False   # 未改动段保持默认


def test_save_config_values_appends_missing_key(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[provider]\nname = "none"\n', encoding="utf-8")
    save_config_values({"provider": {"model": "qwen"}}, p)
    assert load_config(p)["provider"]["model"] == "qwen"


def test_save_config_values_creates_missing_section(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[provider]\nname = "none"\n', encoding="utf-8")
    save_config_values({"retention": {"enabled": True, "cold_days": 30}}, p)
    cfg = load_config(p)
    assert cfg["retention"]["enabled"] is True
    assert cfg["retention"]["cold_days"] == 30
    assert cfg["provider"]["name"] == "none"


def test_save_config_values_quotes_are_escaped(tmp_path):
    p = tmp_path / "c.toml"
    write_default_config(p)
    save_config_values({"provider": {"api_key": 'a"b\\c'}}, p)
    assert load_config(p)["provider"]["api_key"] == 'a"b\\c'
