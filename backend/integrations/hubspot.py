import os
# hubspot.py
import json
import secrets
import base64
import hashlib
from typing import List

import httpx
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse

from integrations.integration_item import IntegrationItem
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis

# === Replace these with your HubSpot app credentials ===
CLIENT_ID = os.environ.get("HUBSPOT_CLIENT_ID", "XXX")
CLIENT_SECRET = os.environ.get("HUBSPOT_CLIENT_SECRET", "XXX")
REDIRECT_URI = os.environ.get("HUBSPOT_REDIRECT_URI", "http://localhost:8000/integrations/hubspot/oauth2callback")
SCOPES = os.environ.get("HUBSPOT_SCOPES", "crm.objects.contacts.read crm.objects.companies.read crm.objects.deals.read")

AUTH_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"

async def authorize_hubspot(user_id, org_id):
    if CLIENT_ID == "XXX" or CLIENT_SECRET == "XXX":
        raise HTTPException(status_code=400, detail="Set HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET env vars (or edit hubspot.py).")
    # CSRF state
    state = secrets.token_urlsafe(32)
    state_payload = json.dumps({"state": state, "user_id": user_id, "org_id": org_id})
    encoded_state = base64.urlsafe_b64encode(state_payload.encode()).decode()
    await add_key_value_redis(f"hubspot_state:{org_id}:{user_id}", state, expire=600)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": encoded_state,
    }
    # Build URL
    query = "&".join([f"{k}={httpx.QueryParams({k:v})[k]}" for k,v in params.items()])
    return f"{AUTH_URL}?{query}"

async def oauth2callback_hubspot(request: Request):
    if request.query_params.get("error"):
        raise HTTPException(status_code=400, detail=request.query_params.get("error"))

    code = request.query_params.get("code")
    encoded_state = request.query_params.get("state")
    try:
        state_data = json.loads(base64.urlsafe_b64decode(encoded_state.encode()).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state.")
    original_state = state_data.get("state")
    user_id = state_data.get("user_id")
    org_id = state_data.get("org_id")

    saved_state = await get_value_redis(f"hubspot_state:{org_id}:{user_id}")
    if not saved_state or saved_state.decode() != original_state:
        raise HTTPException(status_code=400, detail="State mismatch.")

    # Exchange code
    async with httpx.AsyncClient() as client:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "code": code,
        }
        token_resp = await client.post(TOKEN_URL, data=data, headers=headers)
        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_resp.text}")
        tokens = token_resp.json()

    # Save credentials (access + refresh + expires_in) temporarily for pickup by frontend
    await add_key_value_redis(f"hubspot_credentials:{org_id}:{user_id}", json.dumps(tokens), expire=900)
    await delete_key_redis(f"hubspot_state:{org_id}:{user_id}")

    # Close the popup
    html = """
    <html><body>
    <script>window.close();</script>
    HubSpot authorization complete. You can close this window.
    </body></html>
    """
    return HTMLResponse(content=html)

async def get_hubspot_credentials(user_id, org_id):
    credentials = await get_value_redis(f"hubspot_credentials:{org_id}:{user_id}")
    if not credentials:
        raise HTTPException(status_code=400, detail="No credentials found.")
    await delete_key_redis(f"hubspot_credentials:{org_id}:{user_id}")
    return json.loads(credentials)

def create_integration_item_metadata_object(item: dict, item_type: str) -> IntegrationItem:
    # HubSpot common fields: id, properties.name/firstname+lastname, createdAt, updatedAt, archived, etc.
    name = None
    props = item.get("properties", {}) if isinstance(item.get("properties"), dict) else {}
    if item_type == "Contact":
        name = " ".join([props.get("firstname",""), props.get("lastname","")]).strip() or props.get("email")
    elif item_type == "Company":
        name = props.get("name") or props.get("domain")
    elif item_type == "Deal":
        name = props.get("dealname")
    else:
        name = props.get("name") or item.get("id")

    url = None  # Could be composed if you have portalId; omitted here
    created = item.get("createdAt")
    updated = item.get("updatedAt")

    return IntegrationItem(
        id=f"{item.get('id')}_{item_type}",
        type=item_type,
        directory=False,
        parent_path_or_name=None,
        parent_id=None,
        name=name,
        creation_time=created,
        last_modified_time=updated,
        url=url,
        children=None,
        mime_type=None,
        delta=None,
        drive_id=None,
        visibility=None,
    ).__dict__

async def _fetch_paginated(client: httpx.AsyncClient, url: str, headers: dict, item_type: str) -> List[dict]:
    items: List[dict] = []
    after = None
    while True:
        params = {"limit": 100}
        if after:
            params["after"] = after
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            break
        data = resp.json()
        results = data.get("results", [])
        items.extend([create_integration_item_metadata_object(r, item_type) for r in results])
        paging = data.get("paging", {})
        next_link = paging.get("next", {}).get("after")
        if next_link:
            after = next_link
        else:
            break
    return items

async def get_items_hubspot(credentials):
    # credentials is a JSON string from the frontend
    if isinstance(credentials, str):
        try:
            credentials = json.loads(credentials)
        except Exception:
            pass
    access_token = credentials.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Missing access_token in credentials.")

    headers = {"Authorization": f"Bearer {access_token}"}
    base = "https://api.hubapi.com/crm/v3/objects"

    async with httpx.AsyncClient(timeout=20) as client:
        contacts = await _fetch_paginated(client, f"{base}/contacts", headers, "Contact")
        companies = await _fetch_paginated(client, f"{base}/companies", headers, "Company")
        deals = await _fetch_paginated(client, f"{base}/deals", headers, "Deal")

    all_items = contacts + companies + deals
    # Returning a list of IntegrationItem dicts; frontend will print to console
    return all_items
