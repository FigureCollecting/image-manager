import pytest

pytestmark = pytest.mark.skip("integration test requires S3/DB/Redis services")


def test_placeholder():
    assert True

