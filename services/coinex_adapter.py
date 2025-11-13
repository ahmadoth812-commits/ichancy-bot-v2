import time
import hmac
import hashlib
import aiohttp
from urllib.parse import urlencode
import config

COINEX_BASE = "https://api.coinex.com"

def sign_payload_v2(secret: str, method: str, path: str, params: dict = None):
    params = params or {}
    
    timestamp = str(int(time.time()))
    sorted_params = sorted(params.items())
    query_string = urlencode(sorted_params)
    
    sign_text = f"{method}\n{path}\n{query_string}\n{timestamp}"
    
    signature = hmac.new(
        secret.encode('utf-8'),
        sign_text.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return signature, timestamp


class CoinExClient:
    def __init__(self, access_id: str, secret_key: str):
        self.access_id = access_id
        self.secret_key = secret_key
        self._session = None

    async def _get_session(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    def _headers(self, signature: str, timestamp: str):
        return {
            "Content-Type": "application/json",
            "X-COINEX-ACCESS-ID": self.access_id,
            "X-COINEX-SIGN": signature,
            "X-COINEX-TIMESTAMP": timestamp,
        }

    async def _request(self, method: str, path: str, params: dict = None):
        params = params or {}
        signature, timestamp = sign_payload_v2(self.secret_key, method, path, params)
        headers = self._headers(signature, timestamp)
        url = COINEX_BASE + path
        
        session = await self._get_session()
        try:
            if method == "GET":
                async with session.get(url, params=params, headers=headers, timeout=15) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            elif method == "POST":
                async with session.post(url, json=params, headers=headers, timeout=15) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            else:
                raise ValueError(f"Unsupported method: {method}")
                
        except aiohttp.ClientError as e:
            return {"code": -1, "message": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"code": -1, "message": f"Unexpected error: {str(e)}"}

    async def get_deposit_address(self, coin: str, chain: str = None):
        path = "/v2/account/deposit/address"
        params = {"coin": coin}
        if chain:
            params["chain"] = chain
        return await self._request("GET", path, params)

    async def get_deposit_history(self, coin: str, chain: str = None, limit: int = 10):
        path = "/v2/account/deposit/history"
        params = {"coin": coin, "limit": limit}
        if chain:
            params["chain"] = chain
        return await self._request("GET", path, params)

    async def withdraw(self, coin: str, to_address: str, amount: float, chain: str = None):
        path = "/v2/account/withdraw"
        params = {
            "coin": coin,
            "address": to_address,
            "amount": str(amount),
        }
        if chain:
            params["chain"] = chain
        return await self._request("POST", path, params)

    async def get_balance(self):
        path = "/v2/account/balance"
        return await self._request("GET", path)

    async def close(self):
        if self._session:
            await self._session.close()


_coinex_client = None

def get_coinex_client():
    global _coinex_client
    if _coinex_client is None:
        if not config.COINEX_ACCESS_ID or not config.COINEX_SECRET_KEY:
            raise ValueError("CoinEx API credentials not configured in config.py")
        _coinex_client = CoinExClient(
            access_id=config.COINEX_ACCESS_ID,
            secret_key=config.COINEX_SECRET_KEY
        )
    return _coinex_client


async def get_deposit_address(coin: str, chain: str):
    client = get_coinex_client()
    return await client.get_deposit_address(coin, chain)

async def get_deposit_history(coin: str, chain: str = None, limit: int = 10):
    client = get_coinex_client()
    return await client.get_deposit_history(coin, chain, limit)

async def withdraw_coinex(coin: str, to_address: str, amount: float, chain: str = None):
    client = get_coinex_client()
    return await client.withdraw(coin, to_address, amount, chain)

async def get_coinex_balance():
    client = get_coinex_client()
    return await client.get_balance()