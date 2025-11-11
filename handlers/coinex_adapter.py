
import time, hmac, hashlib, json, requests # Added requests import
from urllib.parse import urlencode
import config # Import config for API keys

COINEX_BASE = "https://api.coinex.com"  # Base URL for CoinEx API

def timestamp_ms():
    return int(time.time() * 1000)

def sign_payload(secret: str, params: dict) -> str:
    """
    Generate HMAC-SHA256 signature for CoinEx API.
    NOTE: CoinEx signature scheme can be complex. Verify with their latest API documentation.
    Example: Concatenate sorted params, then HMAC-SHA256 with secret.
    """
    sorted_params = sorted(params.items())
    query_string = urlencode(sorted_params)
    mac = hmac.new(secret.encode(), query_string.encode(), hashlib.sha256)
    return mac.hexdigest()

class CoinExClient:
    def __init__(self, api_key: str, api_secret: str, access_id: str = None):
        self.key = api_key
        self.secret = api_secret
        self.access_id = access_id # CoinEx usually uses Access ID as part of key

    def _headers(self, sign: str):
        # NOTE: Verify CoinEx expects these specific headers for authentication
        return {
            "Content-Type": "application/json",
            "X-ACCESS-KEY": self.key,
            "X-ACCESS-SIGN": sign,
            "X-ACCESS-TIMESTAMP": str(timestamp_ms()),
        }

    def _request(self, method: str, path: str, params: dict = None, data: dict = None):
        url = COINEX_BASE + path
        params = params or {}
        data = data or {}

        # Add common params, if required by CoinEx for signing (e.g., timestamp, access_id)
        # Assuming timestamp is part of the signed payload, not just header for some endpoints
        # params["access_id"] = self.access_id # If access_id needed in payload for signing

        sign = sign_payload(self.secret, params if method == "GET" else {**params, **data}) # Adjust signing for GET vs POST
        headers = self._headers(sign)

        try:
            if method == "GET":
                resp = requests.get(url, params=params, headers=headers, timeout=15)
            elif method == "POST":
                resp = requests.post(url, json=data, headers=headers, timeout=15) # POST body uses 'data'
            else:
                raise ValueError("Unsupported HTTP method")

            resp.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            return resp.json()
        except requests.exceptions.HTTPError as e:
            print(f"HTTP Error: {e.response.status_code} - {e.response.text}")
            return {"error": "http-error", "status_code": e.response.status_code, "text": e.response.text}
        except requests.exceptions.RequestException as e:
            print(f"Request Error: {e}")
            return {"error": "request-failed", "error_desc": str(e)}
        except json.JSONDecodeError:
            print(f"JSON Decode Error: Non-JSON response - {resp.text}")
            return {"error": "non-json-response", "status_code": resp.status_code, "text": resp.text}
        except Exception as e:
            print(f"Unexpected Error: {e}")
            return {"error": "unexpected-error", "error_desc": str(e)}

    def get_deposit_address(self, coin: str, chain: str = None):
        """
        Get deposit address for a specific coin and chain.
        NOTE: CoinEx API for this might be a GET request and different path.
        Example (hypothetical GET path): /api/v2/spot/deposit-address
        """
        path = "/v2/account/deposit_address" # This is likely a POST with params in body for /v2
        params = {"coin": coin}
        if chain:
            params["chain"] = chain # Assuming chain is passed in body

        # For /v2/account/deposit_address, it usually expects POST with params in JSON body.
        # The sign_payload should be adapted if CoinEx expects signing based on different parts.
        return self._request("POST", path, data=params) # Use 'data' for JSON body

    def get_deposit_history(self, coin: str, chain: str = None, limit: int = 10, offset: int = 0):
        """
        Get deposit history for a coin.
        NOTE: This endpoint likely requires GET and a different path.
        Example (hypothetical GET path): /api/v2/spot/deposit-history
        """
        path = "/v2/account/deposit_history" # Hypothetical path, verify with CoinEx docs
        params = {"coin": coin, "limit": limit, "offset": offset}
        if chain:
            params["chain"] = chain
        
        # This is typically a GET request.
        return self._request("GET", path, params=params)

    def withdraw(self, coin: str, to_address: str, amount: float, chain: str = None, memo: str = None):
        """
        Call withdraw endpoint.
        NOTE: address whitelisting, correct signature, and 2FA are critical.
        """
        path = "/v2/account/withdraw" # Hypothetical path, verify with CoinEx docs
        params = {
            "coin": coin,
            "address": to_address,
            "amount": str(amount), # amount might need to be string
            # "client_id": "unique_id_for_this_withdrawal" # Some exchanges require this
        }
        if chain:
            params["chain"] = chain
        if memo:
            params["memo"] = memo

        return self._request("POST", path, data=params) # Use 'data' for JSON body

# Global client instance (initially None, set when API keys are available)
_coinex_client = None

def get_coinex_client():
    global _coinex_client
    if _coinex_client is None:
        if not config.COINEX_API_KEY or not config.COINEX_API_SECRET:
            raise ValueError("CoinEx API Key or Secret not configured in config.py")
        _coinex_client = CoinExClient(
            api_key=config.COINEX_API_KEY,
            api_secret=config.COINEX_API_SECRET,
            access_id=config.COINEX_ACCESS_ID # Pass access_id if used
        )
    return _coinex_client

# Simple wrapper functions for convenience
async def get_deposit_address(coin: str, chain: str):
    client = get_coinex_client()
    return client.get_deposit_address(coin, chain)

async def get_deposit_history(coin: str, chain: str = None, limit: int = 10):
    client = get_coinex_client()
    return client.get_deposit_history(coin, chain, limit)

async def withdraw_coinex(coin: str, to_address: str, amount: float, chain: str = None, memo: str = None):
    client = get_coinex_client()
    return client.withdraw(coin, to_address, amount, chain, memo)