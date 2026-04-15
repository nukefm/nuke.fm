from __future__ import annotations

from datetime import UTC, datetime, timedelta

import base58
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from solders.pubkey import Pubkey

from .accounts import AccountStore, AuthenticatedUser
from .database import utc_now


class AuthService:
    def __init__(self, *, app_name: str, challenge_ttl_seconds: int, account_store: AccountStore) -> None:
        self._app_name = app_name
        self._challenge_ttl_seconds = challenge_ttl_seconds
        self._account_store = account_store

    def create_challenge(self, wallet_address: str) -> dict:
        normalized_wallet_address = self._normalize_wallet_address(wallet_address)
        expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=self._challenge_ttl_seconds)
        challenge_message = (
            f"{self._app_name} API key challenge\n"
            f"Wallet: {normalized_wallet_address}\n"
            f"Expires: {expires_at.isoformat()}"
        )
        return self._account_store.issue_challenge(
            normalized_wallet_address,
            challenge_message,
            expires_at.isoformat(),
        )

    def exchange_api_key(self, *, wallet_address: str, challenge_id: str, signature: str) -> dict:
        normalized_wallet_address = self._normalize_wallet_address(wallet_address)
        challenge = self._account_store.get_challenge(challenge_id)
        if challenge is None:
            raise ValueError("Unknown challenge id.")
        if challenge["wallet_address"] != normalized_wallet_address:
            raise ValueError("Challenge wallet does not match the requested wallet.")
        if challenge["consumed_at"] is not None:
            raise ValueError("Challenge has already been consumed.")
        if challenge["expires_at"] <= utc_now():
            raise ValueError("Challenge has expired.")

        self._verify_wallet_signature(
            wallet_address=normalized_wallet_address,
            challenge_message=challenge["challenge_message"],
            signature=signature,
        )

        user = self._account_store.ensure_user(normalized_wallet_address)
        api_key = self._account_store.issue_api_key(user["id"])
        self._account_store.consume_challenge(challenge_id, utc_now())

        return {
            "wallet_address": normalized_wallet_address,
            "api_key": api_key["api_key"],
            "created_at": api_key["created_at"],
        }

    def authenticate_api_key(self, raw_api_key: str | None) -> AuthenticatedUser | None:
        if raw_api_key is None:
            return None
        return self._account_store.authenticate_api_key(raw_api_key)

    @staticmethod
    def _normalize_wallet_address(wallet_address: str) -> str:
        return str(Pubkey.from_string(wallet_address))

    @staticmethod
    def _verify_wallet_signature(*, wallet_address: str, challenge_message: str, signature: str) -> None:
        verify_key = VerifyKey(base58.b58decode(wallet_address))
        try:
            verify_key.verify(challenge_message.encode("utf-8"), base58.b58decode(signature))
        except BadSignatureError as error:
            raise ValueError("Invalid wallet signature.") from error
