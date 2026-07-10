"""iter_leads_for_export() must yield rows via a server-side cursor instead
of loading the whole result set into memory (see get_all_leads_for_export's
old .all()-based sibling, which stays for campaigns.py's lead_ids-bounded
callers), and the /api/leads/export endpoint must actually stream that
output rather than building the full CSV in memory first."""

import types

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud.api import leads as leads_api
from cloud.api.deps import CurrentUser, get_current_user, get_db
from cloud.db.database import Database


def _make_db(tmp_path) -> Database:
    config = {
        "database": {"uri": f"sqlite:///{tmp_path}/test.db"},
        "auth": {"credential_enc_key": Fernet.generate_key().decode()},
    }
    return Database(config, config_path=tmp_path / "config.yaml")


def _seed_leads(db, job_id, n):
    rows = [
        {"email": f"p{i}@x.gov.in", "name": f"P{i}", "designation": "Officer", "department": "Dept", "phone": str(i)}
        for i in range(n)
    ]
    db.bulk_upsert_manual_leads(job_id=job_id, rows=rows)


def test_iter_leads_for_export_is_a_generator_and_matches_get_all(tmp_path):
    db = _make_db(tmp_path)
    _seed_leads(db, job_id=1, n=5)

    it = db.iter_leads_for_export(job_ids=[1])
    assert isinstance(it, types.GeneratorType)

    streamed = list(it)
    bulk = db.get_all_leads_for_export(job_ids=[1])
    assert len(streamed) == len(bulk) == 5
    assert {r["email"] for r in streamed} == {r["email"] for r in bulk}


def test_export_endpoint_streams_csv(tmp_path):
    db = _make_db(tmp_path)
    _seed_leads(db, job_id=1, n=5)

    app = FastAPI()
    app.include_router(leads_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=1, email="a@a.com", is_admin=True)
    client = TestClient(app)

    resp = client.post("/api/leads/export", json={"job_ids": [1]})
    assert resp.status_code == 200
    lines = [line for line in resp.text.strip().split("\n") if line]
    assert len(lines) == 6  # header + 5 rows


def test_export_endpoint_404s_on_no_matching_leads(tmp_path):
    db = _make_db(tmp_path)
    _seed_leads(db, job_id=1, n=1)

    app = FastAPI()
    app.include_router(leads_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=1, email="a@a.com", is_admin=True)
    client = TestClient(app)

    resp = client.post("/api/leads/export", json={"job_ids": [999]})
    assert resp.status_code == 404
