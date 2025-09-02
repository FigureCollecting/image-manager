import pytest

pytestmark = pytest.mark.skip("integration test requires S3/DB/Redis services")


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200

