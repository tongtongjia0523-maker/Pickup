from ai_memory.paths import default_db_path, project_key


def test_project_key_sanitizes_windows_path():
    assert project_key("D:\\AI-test") == "D--AI-test"


def test_project_key_sanitizes_slashes():
    assert project_key("/home/user/proj") == "-home-user-proj"


def test_default_db_path_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path))
    p = default_db_path()
    assert p == tmp_path / "memory.db"


def test_default_db_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "custom.db"))
    assert default_db_path() == tmp_path / "custom.db"
