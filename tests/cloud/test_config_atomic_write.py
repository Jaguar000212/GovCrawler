"""save_config() writes app_settings (transactional) before touching
config.yaml, and the yaml write itself is atomic (temp file + os.replace) —
a crash mid-write can't corrupt the file or leave the DB/yaml disagreeing."""

from pathlib import Path

import yaml
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.api import config as config_api
from cloud.api.deps import CurrentUser, get_config as get_app_config, get_config_path, get_current_user, get_db
from cloud.db.database import Database


def _make_db(tmp_path) -> Database:
    config = {
        "database": {"uri": f"sqlite:///{tmp_path}/test.db"},
        "auth": {"credential_enc_key": Fernet.generate_key().decode()},
    }
    return Database(config, config_path=tmp_path / "config.yaml")


def _make_client(tmp_path, db, c, cfg_path):
    app = FastAPI()
    app.include_router(config_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=1, email="a@a.com", is_admin=True)
    app.dependency_overrides[get_app_config] = lambda: c
    app.dependency_overrides[get_config_path] = lambda: cfg_path
    return TestClient(app)


def test_save_config_writes_yaml_and_app_settings(tmp_path):
    db = _make_db(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    c = {
        "crawler": {
            "workers": 4,
            "per_url_timeout": 10,
            "playwright_timeout": 20,
            "js_settle_time": 1.0,
            "httpx_first": True,
            "playwright_fallback": True,
        }
    }
    with open(cfg_path, "w") as f:
        yaml.dump(c, f)

    client = _make_client(tmp_path, db, c, cfg_path)
    resp = client.post("/api/config", json={"workers": 8, "max_depth": 6})
    assert resp.status_code == 200

    with open(cfg_path) as f:
        on_disk = yaml.safe_load(f)
    assert on_disk["crawler"]["workers"] == 8

    policy = db.get_crawl_policy()
    assert policy["crawler"]["max_depth"] == 6


def test_save_config_leaves_no_tmp_file_behind(tmp_path):
    db = _make_db(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    c = {
        "crawler": {
            "workers": 4,
            "per_url_timeout": 10,
            "playwright_timeout": 20,
            "js_settle_time": 1.0,
            "httpx_first": True,
            "playwright_fallback": True,
        }
    }
    with open(cfg_path, "w") as f:
        yaml.dump(c, f)

    client = _make_client(tmp_path, db, c, cfg_path)
    resp = client.post("/api/config", json={"workers": 8})
    assert resp.status_code == 200

    tmp_leftover = Path(str(cfg_path) + ".tmp")
    assert not tmp_leftover.exists()
