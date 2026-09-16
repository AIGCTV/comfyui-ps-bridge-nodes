"""Server-issued credentials bind client IDs to roles before dispatch."""
from __future__ import annotations
import hmac
import secrets
from .errors import require
from .json_codec import byte_digest
from .parameters import identifier
from .storage import atomic_json, read_json, LOCK

class ClientIdentities:
    def __init__(self, path):
        self.path = path

    def enroll(self, client_id, role, previous_token=None):
        identifier(client_id, "clientId")
        require(role in {"controller", "editor", "observer"}, "FORBIDDEN", "Unsupported client role", status=403)
        with LOCK:
            clients = read_json(self.path, {})
            if client_id in clients:
                self.authenticate(client_id, previous_token, role)
                return {"clientId": client_id, "role": role, "clientToken": previous_token}
            token = secrets.token_urlsafe(32)
            clients[client_id] = {"role": role, "tokenHash": byte_digest(token.encode("utf-8"))}
            atomic_json(self.path, clients)
            return {"clientId": client_id, "role": role, "clientToken": token}

    def authenticate(self, client_id, token, role=None):
        require(isinstance(client_id, str) and isinstance(token, str), "UNAUTHENTICATED", "Client credential required", status=401)
        with LOCK:
            record = read_json(self.path, {}).get(client_id)
            require(record is not None and hmac.compare_digest(record["tokenHash"], byte_digest(token.encode("utf-8"))),
                    "UNAUTHENTICATED", "Invalid client credential", status=401)
            require(role is None or record["role"] == role, "FORBIDDEN", "Credential role mismatch", status=403)
            return record["role"]
