"""Seal transient browser secrets to the worker; API needs only its public key."""

import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii")


def decode(value):
    return base64.urlsafe_b64decode(value.encode("ascii"))


def derive(value, scope):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=scope).derive(value)


def worker_private_key(path):
    value = base64.urlsafe_b64decode(Path(path).read_bytes().strip())
    if len(value) != 32:
        raise ValueError("Worker key is invalid")
    return X25519PrivateKey.from_private_bytes(derive(value, b"archive-desk/browser-onboarding/v1"))


def public_key(private):
    return encode(
        private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )


def key_id(public):
    return hashlib.sha256(decode(public)).hexdigest()


def seal(public, value, scope):
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(decode(public)))
    nonce = os.urandom(12)
    ciphertext = AESGCM(derive(shared, scope.encode())).encrypt(
        nonce, value.encode(), scope.encode()
    )
    return {
        "ephemeral": public_key(ephemeral),
        "nonce": encode(nonce),
        "ciphertext": encode(ciphertext),
    }


def unseal(private, envelope, scope):
    shared = private.exchange(X25519PublicKey.from_public_bytes(decode(envelope["ephemeral"])))
    return (
        AESGCM(derive(shared, scope.encode()))
        .decrypt(decode(envelope["nonce"]), decode(envelope["ciphertext"]), scope.encode())
        .decode()
    )
