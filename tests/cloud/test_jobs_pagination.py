"""GET /api/jobs had no offset pagination (capped at limit<=100, no way to
page further). list_jobs() now takes a page param and returns (jobs, total)."""

from cryptography.fernet import Fernet

from cloud.db.database import Database


def _make_db(tmp_path) -> Database:
    config = {
        "database": {"uri": f"sqlite:///{tmp_path}/test.db"},
        "auth": {"credential_enc_key": Fernet.generate_key().decode()},
    }
    return Database(config, config_path=tmp_path / "config.yaml")


def test_list_jobs_pages_without_overlap(tmp_path):
    db = _make_db(tmp_path)
    uid = db.create_user("a@a.com", "pw", is_admin=True)
    for _ in range(5):
        db.create_job(owner_id=uid, domain_ids=[], category_filter=None, title_filter=None)

    page1, total1 = db.list_jobs(limit=2, page=1, owner_id=uid, view_all=False)
    page2, total2 = db.list_jobs(limit=2, page=2, owner_id=uid, view_all=False)
    page3, total3 = db.list_jobs(limit=2, page=3, owner_id=uid, view_all=False)

    assert total1 == total2 == total3 == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1  # tail page, fewer than `limit`

    seen_ids = {j["id"] for j in page1} | {j["id"] for j in page2} | {j["id"] for j in page3}
    assert len(seen_ids) == 5  # every job seen exactly once across pages
