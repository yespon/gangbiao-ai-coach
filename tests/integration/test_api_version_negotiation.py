def test_legacy_api_defaults_to_v1_and_exposes_negotiation_headers(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers.get("x-api-version") == "1"
    assert response.headers.get("x-api-legacy") == "true"


def test_legacy_api_rejects_unsupported_requested_version(client):
    response = client.get("/api/health", headers={"x-api-version": "2"})
    assert response.status_code == 400
    assert "不支持的 API 版本" in response.json().get("detail", "")


def test_legacy_api_accepts_supported_query_version(client):
    response = client.get("/api/health?api_version=1")
    assert response.status_code == 200
    assert response.headers.get("x-api-version") == "1"
    assert response.headers.get("x-api-legacy") == "true"


def test_legacy_api_rejects_unsupported_query_version(client):
    response = client.get("/api/health?api_version=2")
    assert response.status_code == 400
    assert "不支持的 API 版本" in response.json().get("detail", "")


def test_legacy_api_header_takes_precedence_over_query(client):
    response = client.get("/api/health?api_version=2", headers={"x-api-version": "1"})
    assert response.status_code == 200
    assert response.headers.get("x-api-version") == "1"
