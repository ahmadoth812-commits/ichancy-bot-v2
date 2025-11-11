# coinex_adapter.py
# Minimal adapter for CoinEx deposit/withdraw.
# NOTE: adjust sign_payload() to match exact CoinEx signature scheme if API rejects signatures.

import time, hmac, hashlib, json, requests
from urllib.parse import urlencode

COINEX_BASE = "https://api.coinex.com"  # change if needed

def timestamp_ms():
    return int(time.time() * 1000)

def sign_payload(secret: str, params: dict) -> str:
    """
    Default signing: HMAC-SHA256 hex digest of query string.
    Many exchanges use HMAC-SHA256(secret, message) where message is sorted query string.
    If CoinEx expects a different format (base64, header names, ordering), adjust here.
    """
    # ensure deterministic ordering
    qs = urlencode(sorted(params.items()))
    mac = hmac.new(secret.encode(), qs.encode(), hashlib.sha256)
    return mac.hexdigest()

class CoinExClient:
    def __init__(self, api_key: str, api_secret: str, access_id: str = None):
        """
        access_id: some docs refer to access_id / access_key. Keep for compatibility.
        """
        self.key = api_key
        self.secret = api_secret
        self.access_id = access_id

    def _headers(self, sign: str):
        # Default header names; adjust if CoinEx expects different names.
        return {
            "Content-Type": "application/json",
            "X-ACCESS-KEY": self.key,
            "X-ACCESS-SIGN": sign,
            "X-ACCESS-TIMESTAMP": str(timestamp_ms()),
        }

    def _post(self, path: str, params: dict):
        url = COINEX_BASE + path
        try:
            resp = requests.post(url, json=params, timeout=15)
            try:
                return resp.json()
            except Exception:
                return {"error": "non-json-response", "status_code": resp.status_code, "text": resp.text}
        except Exception as e:
            return {"error": "request-failed", "error_desc": str(e)}

    def get_deposit_address(self, coin: str, network: str = None):
        """
        Try to call endpoint that returns deposit address.
        If CoinEx doc uses GET or different path, adapt path & params accordingly.
        """
        path = "/v2/account/deposit_address"  # example path — adapt if your CoinEx doc shows different.
        params = {
            "coin": coin,
            "timestamp": timestamp_ms(),
        }
        if network:
            params["network"] = network
        sign = sign_payload(self.secret, params)
        headers = self._headers(sign)
        # Some implementations put signature in query; here we send in headers and body
        resp = requests.post(COINEX_BASE + path, json=params, headers=headers, timeout=15)
        try:
            return resp.json()
        except:
            return {"error": "non-json", "text": resp.text, "status_code": resp.status_code}

    def withdraw(self, coin: str, to_address: str, amount: float, network: str = None, memo: str = None):
        """
        Call withdraw endpoint. IMPORTANT: most exchanges require:
          - address whitelisted for API (see docs)
          - correct signature
          - 2FA enabled or other account-level settings

        Returns parsed JSON or error dict.
        """
        path = "/v2/account/withdraw"  # example — adapt to actual CoinEx withdraw endpoint
        params = {
            "coin": coin,
            "address": to_address,
            "amount": str(amount),
            "timestamp": timestamp_ms()
        }
        if network:
            params["network"] = network
        if memo:
            params["memo"] = memo

        # sign and headers
        sign = sign_payload(self.secret, params)
        headers = self._headers(sign)
        resp = requests.post(COINEX_BASE + path, json=params, headers=headers, timeout=20)
        try:
            return resp.json()
        except:
            return {"error": "non-json", "text": resp.text, "status_code": resp.status_code}
