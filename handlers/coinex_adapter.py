import time, hmac, hashlib, json, requests
from urllib.parse import urlencode
import config
import asyncio

COINEX_BASE = "https://api.coinex.com"  # v2 endpoints are under /v2/

def timestamp_ms():
    """Return current timestamp in milliseconds as string."""
    return str(int(time.time() * 1000))

def sign_payload(secret: str, method: str, request_path: str, query_string: str, body_str: str, timestamp: str) -> str:
    """
    Build signature according to CoinEx v2 rules:
    prepared_str = METHOD + request_path(+ '?' + query_string if exists) + body(optional) + timestamp
    sign = HMAC_SHA256(secret, prepared_str).hexdigest().lower()
    """
    path = request_path
    if query_string:
        path = f"{request_path}?{query_string}"
    prepared = f"{method.upper()}{path}{body_str}{timestamp}"
    mac = hmac.new(secret.encode('utf-8'), prepared.encode('utf-8'), hashlib.sha256)
    return mac.hexdigest().lower()


class CoinExClient:
    def __init__(self, access_id: str, secret_key: str):
        self.access_id = access_id
        self.secret_key = secret_key

    def _headers(self, sign: str, ts: str):
        return {
            "Content-Type": "application/json",
            "X-COINEX-KEY": self.access_id,
            "X-COINEX-SIGN": sign,
            "X-COINEX-TIMESTAMP": ts,
        }

    def _request(self, method: str, path: str, params: dict = None, data: dict = None):
        method = method.upper()
        params = params or {}
        data = data or {}

        request_path = f"/v2{path}"
        query_string = urlencode(params) if method == "GET" and params else ""
        body_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False) if method == "POST" else ""

        ts = timestamp_ms()
        sign = sign_payload(self.secret_key, method, request_path, query_string, body_str, ts)
        headers = self._headers(sign, ts)
        url = COINEX_BASE + request_path

        try:
            if method == "GET":
                resp = requests.get(url, params=params, headers=headers, timeout=15)
            elif method == "POST":
                resp = requests.post(url, json=data, headers=headers, timeout=15)
            else:
                raise ValueError("Unsupported HTTP method")

            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            return {"error": "http-error", "status_code": e.response.status_code, "text": e.response.text}
        except requests.exceptions.RequestException as e:
            return {"error": "request-failed", "error_desc": str(e)}
        except Exception as e:
            return {"error": "unexpected-error", "error_desc": str(e)}

    def get_deposit_address(self, coin: str, chain: str = None):
        path = "/assets/deposit-address"
        params = {"ccy": coin}
        if chain:
            params["chain"] = chain
        return self._request("GET", path, params=params)

    def get_deposit_history(self, coin: str, chain: str = None, limit: int = 10, page: int = 1):
        path = "/assets/deposit-history"
        params = {"ccy": coin, "limit": limit, "page": page}
        if chain:
            params["chain"] = chain
        return self._request("GET", path, params=params)

    def withdraw(self, coin: str, to_address: str, amount: float, chain: str = None, memo: str = None, extra: dict = None):
        path = "/assets/withdraw"
        data = {"ccy": coin, "to_address": to_address, "amount": str(amount)}
        if chain:
            data["chain"] = chain
        if memo:
            data["memo"] = memo
        if extra:
            data["extra"] = extra
        return self._request("POST", path, data=data)


_coinex_client = None

def get_coinex_client():
    global _coinex_client
    if _coinex_client is None:
        if not config.COINEX_ACCESS_ID or not config.COINEX_SECRET_KEY:
            raise ValueError("CoinEx credentials not set in config.py")
        _coinex_client = CoinExClient(
            access_id=config.COINEX_ACCESS_ID,
            secret_key=config.COINEX_SECRET_KEY
        )
    return _coinex_client


# Async wrappers
async def get_deposit_address(coin: str, chain: str = None):
    client = get_coinex_client()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, client.get_deposit_address, coin, chain)

async def get_deposit_history(coin: str, chain: str = None, limit: int = 10, page: int = 1):
    client = get_coinex_client()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, client.get_deposit_history, coin, chain, limit, page)

async def withdraw_coinex(coin: str, to_address: str, amount: float, chain: str = None, memo: str = None):
    client = get_coinex_client()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, client.withdraw, coin, to_address, amount, chain, memo)
