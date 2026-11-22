def test_health(client):
    data = client.get("/health").json()
    assert data == {"status": "ok", "knowledge_articles": 8, "assets": 4}


def test_search_requires_auth(client):
    response = client.post("/api/v1/search", json={"query": "VPN失败", "category": "vpn"})
    assert response.status_code == 401


def test_vpn_search_returns_citation(client, headers):
    response = client.post("/api/v1/search", headers=headers, json={"query": "VPN显示AUTH-401", "category": "vpn"})
    assert response.status_code == 200
    assert response.json()["evidence"][0]["citation_id"] == "IT-KB-VPN-003"


def test_category_isolation(client, headers):
    data = client.post("/api/v1/search", headers=headers, json={"query": "登录失败", "category": "account"}).json()
    assert all(item["category"] == "account" for item in data["evidence"])


def test_asset_lookup(client, headers):
    data = client.get("/api/v1/assets/LAP-1001", headers=headers).json()
    assert data["asset"]["support_group"] == "Endpoint Support"


def test_unknown_asset_is_404(client, headers):
    assert client.get("/api/v1/assets/LAP-9999", headers=headers).status_code == 404


def test_ticket_requires_pending_review(client, headers):
    response = client.post("/api/v1/ticket-drafts", headers=headers, json={"summary": "VPN持续连接失败需要人工处理", "category": "vpn", "priority": "P3", "asset_tag": "LAP-1001", "idempotency_key": "vpn-demo-0001"})
    assert response.status_code == 201
    assert response.json()["status"] == "pending_review"


def test_ticket_idempotency(client, headers):
    payload = {"summary": "VPN持续连接失败需要人工处理", "category": "vpn", "priority": "P3", "idempotency_key": "vpn-demo-0002"}
    first = client.post("/api/v1/ticket-drafts", headers=headers, json=payload).json()
    second = client.post("/api/v1/ticket-drafts", headers=headers, json=payload).json()
    assert second["id"] == first["id"]
    assert second["deduplicated"] is True


def test_review_only_once(client, headers):
    ticket = client.post("/api/v1/ticket-drafts", headers=headers, json={"summary": "邮箱客户端持续异常需要人工处理", "category": "email", "priority": "P3", "idempotency_key": "mail-demo-0001"}).json()
    url = f"/api/v1/ticket-drafts/{ticket['id']}/review"
    assert client.post(url, headers=headers, json={"reviewer": "IT主管", "decision": "approved"}).status_code == 200
    assert client.post(url, headers=headers, json={"reviewer": "IT主管", "decision": "approved"}).status_code == 409


def test_audit_records_actions(client, headers):
    client.post("/api/v1/search", headers=headers, json={"query": "VPN失败", "category": "vpn"})
    client.get("/api/v1/assets/LAP-1001", headers=headers)
    actions = {item["action"] for item in client.get("/api/v1/audit", headers=headers).json()}
    assert {"search", "lookup"}.issubset(actions)
