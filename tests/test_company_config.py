from src import company_config


def test_default_company_is_default():
    company_config.set_company("default")
    assert company_config.get_company() == "default"


def test_set_company_changes_get_company():
    company_config.set_company("a")
    try:
        assert company_config.get_company() == "a"
    finally:
        company_config.set_company("default")


def test_state_path_namespaces_by_company(tmp_path, monkeypatch):
    monkeypatch.setattr(company_config, "REPO_ROOT", tmp_path)
    company_config.set_company("a")
    company_config.set_company("b")  # last call wins, matches process-wide semantics
    try:
        path_b = company_config.state_path("position_state.json")
        assert path_b == tmp_path / "state" / "b" / "position_state.json"
        assert path_b.parent.exists()  # directory created on demand

        company_config.set_company("a")
        path_a = company_config.state_path("position_state.json")
        assert path_a == tmp_path / "state" / "a" / "position_state.json"
        assert path_a != path_b
    finally:
        company_config.set_company("default")


def test_log_dir_namespaces_by_company(tmp_path, monkeypatch):
    monkeypatch.setattr(company_config, "REPO_ROOT", tmp_path)
    company_config.set_company("a")
    try:
        assert company_config.log_dir() == tmp_path / "logs" / "a"
    finally:
        company_config.set_company("default")
