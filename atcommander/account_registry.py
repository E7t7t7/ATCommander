"""Named local accounts. New credentials are kept in Windows Credential Manager."""
from __future__ import annotations

import json
import re
import keyring
from .credentials import Credentials, SERVICE, load_credentials, save_credentials
from .command_store import CommandStore
from .config import APP_DIR


class AccountRegistry:
    def __init__(self, store=None, vault=None):
        self.store = store or CommandStore(APP_DIR / "accounts.json")
        self.vault = vault or keyring

    @staticmethod
    def alias(value):
        value = str(value).strip()
        if not re.fullmatch(r"[\w -]{1,40}", value) or value.lower() == "all":
            raise ValueError("Use an account alias of 1–40 letters, numbers, spaces, dashes or underscores.")
        return value

    def list(self):
        return self.store.read().get("accounts", {})

    def save(self, alias, credentials, role="player"):
        alias = self.alias(alias)
        if not credentials.ready:
            raise ValueError("Both player ID and login hash are required.")
        # Fail visibly when the vault is unavailable; do not silently create an
        # extra plaintext copy for a newly registered account.
        self.vault.set_password(SERVICE, "account:" + alias, json.dumps({"user_id":credentials.user_id,"password_hash":credentials.password_hash}))
        accounts = self.list(); accounts[alias] = {"role":role}
        self.store.update("accounts", accounts)

    def load(self, alias):
        if alias not in self.list():
            raise ValueError("That local account alias does not exist.")
        raw = self.vault.get_password(SERVICE, "account:" + alias)
        if not raw:
            raise ValueError("The saved account credential is unavailable in Windows Credential Manager.")
        data = json.loads(raw)
        return Credentials(str(data["user_id"]), str(data["password_hash"]))

    def remove(self, alias):
        names = list(self.list()) if alias == "all" else [self.alias(alias)]
        accounts = self.list()
        for name in names:
            if name in accounts:
                self.vault.delete_password(SERVICE, "account:" + name)
                del accounts[name]
        self.store.update("accounts", accounts)

    def activate(self, alias):
        selected = self.load(alias)
        current = load_credentials()
        if current.ready and "Main" not in self.list():
            self.save("Main", current)
        save_credentials(selected.user_id, selected.password_hash)
        self.store.update("active", alias)
