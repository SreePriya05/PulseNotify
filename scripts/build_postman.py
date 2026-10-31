"""Regenerate the portable collection without embedding credentials or tokens."""

import json
from pathlib import Path

items = []


def add(name, method, path, code, body=None, token=None, tests=None, pre=None, description=""):
    request = {
        "method": method,
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "url": "{{base_url}}" + path,
        "description": description,
        "auth": {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{" + token + "}}", "type": "string"}],
        }
        if token
        else {"type": "noauth"},
    }
    if body is not None:
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    script = [f"pm.test('HTTP {code}', () => pm.response.to.have.status({code}));"]
    script.extend(tests or [])
    item = {
        "name": name,
        "request": request,
        "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": script}}],
    }
    if pre:
        item["event"].insert(
            0, {"listen": "prerequest", "script": {"type": "text/javascript", "exec": pre}}
        )
    items.append(item)


credentials = {
    "username": "{{username}}",
    "password": "{{password}}",
    "email": "{{username}}@example.com",
    "role": "admin",
}
alert = {"origin": "DEL", "destination": "BOM", "threshold_price": "7000.00"}
add(
    "01 Register a new user (role injection ignored)",
    "POST",
    "/api/auth/register/",
    201,
    credentials,
    tests=[
        "const registrationBody = pm.response.json();",
        "pm.test('JWT returned and role is user', () => { pm.expect(registrationBody.access).to.be.a('string'); pm.expect(registrationBody.role).to.eql('user'); });",
        "pm.collectionVariables.set('access', registrationBody.access);",
    ],
    pre=[
        "pm.collectionVariables.set('username', 'pulse_' + pm.variables.replaceIn('{{$guid}}').replace(/-/g, '').slice(0, 14));"
    ],
)
add("02 Duplicate username", "POST", "/api/auth/register/", 400, credentials)
add(
    "03 Login with valid credentials",
    "POST",
    "/api/auth/login/",
    200,
    credentials,
    tests=["pm.collectionVariables.set('access', pm.response.json().access);"],
)
add(
    "04 Login with wrong password",
    "POST",
    "/api/auth/login/",
    401,
    {"username": "{{username}}", "password": "wrong-password"},
)
add("05 Create alert without token", "POST", "/api/alerts/", 401, alert)
add(
    "06 Create an alert with JWT (guaranteed threshold hit)",
    "POST",
    "/api/alerts/",
    201,
    alert,
    "access",
    tests=[
        "pm.collectionVariables.set('alert_id', pm.response.json().id);",
        "pm.test('Initially active', () => pm.expect(pm.response.json().status).to.eql('active'));",
    ],
)
add(
    "07 List own alerts",
    "GET",
    "/api/alerts/",
    200,
    token="access",
    tests=[
        "pm.test('Own alert present', () => { const rows = pm.response.json(); pm.expect(rows).to.be.an('array'); pm.expect(rows.some(x => String(x.id) === String(pm.collectionVariables.get('alert_id')))).to.be.true; });",
    ],
)
add(
    "08 Deactivate a disposable alert (preserve scenario 06)",
    "DELETE",
    "/api/alerts/{{deactivate_id}}/",
    200,
    token="access",
    tests=[
        "pm.test('Inactive status', () => pm.expect(pm.response.json().status).to.eql('inactive'));",
    ],
    pre=[
        "pm.sendRequest({url: pm.collectionVariables.get('base_url') + '/api/alerts/', method: 'POST', header: {'Content-Type': 'application/json', Authorization: 'Bearer ' + pm.collectionVariables.get('access')}, body: {mode: 'raw', raw: JSON.stringify({origin: 'DEL', destination: 'BOM', threshold_price: '0.01'})}}, (err, res) => {",
        "  if (err || res.code !== 201) throw new Error('Could not create disposable alert');",
        "  pm.collectionVariables.set('deactivate_id', res.json().id);",
        "});",
    ],
)
add(
    "09 Mock feed with valid route",
    "GET",
    "/api/flights/price/?route=DEL-BOM",
    200,
    tests=[
        "pm.test('Integer price within range', () => { const d = pm.response.json(); pm.expect(d.route).to.eql('DEL-BOM'); pm.expect(Number.isInteger(d.price)).to.be.true; pm.expect(d.price).to.be.within(3000, 7000); });"
    ],
)
add("10 Mock feed with invalid route", "GET", "/api/flights/price/?route=XYZ-ABC", 404)
add("11 Summary forbidden for regular user", "GET", "/api/admin/summary/", 403, token="access")
add(
    "12 Summary allowed for admin",
    "GET",
    "/api/admin/summary/",
    200,
    token="admin_access",
    tests=[
        "pm.test('Summary fields', () => { const d = pm.response.json(); ['total_alerts', 'active_alerts', 'triggered_alerts', 'total_notifications'].forEach(k => pm.expect(d[k]).to.be.a('number')); pm.expect(d.top_routes).to.be.an('array'); });"
    ],
    pre=[
        "const username = pm.collectionVariables.get('admin_username'); const password = pm.collectionVariables.get('admin_password');",
        "if (!username || !password) throw new Error('Set admin_username/admin_password after promoting the account with manage.py set_role');",
        "pm.sendRequest({url: pm.collectionVariables.get('base_url') + '/api/auth/login/', method: 'POST', header: {'Content-Type': 'application/json'}, body: {mode: 'raw', raw: JSON.stringify({username, password})}}, (err, res) => {",
        "  if (err || res.code !== 200 || res.json().role !== 'admin') throw new Error('Admin login failed or profile role is not admin');",
        "  pm.collectionVariables.set('admin_access', res.json().access);",
        "});",
    ],
    description="Set local admin credentials first. See README for role promotion.",
)
add(
    "13 Verify notification after Beat fires + manual PGAdmin evidence",
    "GET",
    "/api/admin/summary/",
    200,
    token="admin_access",
    tests=[
        "pm.test('At least one notification exists', () => pm.expect(pm.response.json().total_notifications).to.be.at.least(1));",
    ],
    description="Wait 60–90 seconds with Beat and worker running before this request. This count is supporting evidence. REQUIRED: use the README SQL in PGAdmin with collection alert_id to verify that exact alert has one NotificationLog and triggered status. Capture the row as submission evidence.",
)

collection = {
    "info": {
        "name": "PulseNotify — 13 assignment scenarios",
        "description": "Run in order; configure admin credentials before 12; wait for Beat before 13. Scenario 13 also requires a manual PGAdmin row check. See README.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "variable": [
        {"key": key, "value": value, "type": "string"}
        for key, value in {
            "base_url": "http://localhost:8000",
            "username": "",
            "password": "FlightAlerts!925",
            "access": "",
            "alert_id": "",
            "deactivate_id": "",
            "admin_username": "",
            "admin_password": "",
            "admin_access": "",
        }.items()
    ],
    "item": items,
}
output = Path(__file__).resolve().parents[1] / "postman" / "PulseNotify.postman_collection.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {len(items)} scenarios to {output.name}")
