# fernet_utils.py
from cryptography.fernet import Fernet
import base64
import os

KEY_FILE = "fernet.key"

def generate_key(save_to=KEY_FILE):
    """Generate and save a Fernet key (one-time)."""
    key = Fernet.generate_key()
    with open(save_to, "wb") as f:
        f.write(key)
    return key

def load_key(path=KEY_FILE):
    if not os.path.exists(path):
        return generate_key(path)
    with open(path, "rb") as f:
        return f.read()

def encrypt_text(plain_text: str, key=None):
    if key is None:
        key = load_key()
    f = Fernet(key)
    token = f.encrypt(plain_text.encode("utf-8"))
    return token.decode("utf-8")

def decrypt_text(token_text: str, key=None):
    if key is None:
        key = load_key()
    f = Fernet(key)
    return f.decrypt(token_text.encode("utf-8")).decode("utf-8")

if __name__ == "__main__":
    # quick CLI helpers
    k = load_key()
    print("Fernet key path:", KEY_FILE)
    txt = input("Text to encrypt (or empty to exit): ").strip()
    if txt:
        enc = encrypt_text(txt, k)
        print("Encrypted:", enc)
        print("Decrypted (sanity):", decrypt_text(enc, k))
