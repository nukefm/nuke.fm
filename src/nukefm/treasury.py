from __future__ import annotations

import hashlib
import hmac
import subprocess
from dataclasses import dataclass

from loguru import logger
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import Transaction
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import TransferCheckedParams, create_associated_token_account, get_associated_token_address, transfer_checked

from .accounts import AccountStore
from .amounts import USDC_DECIMALS
from .database import utc_now


@dataclass(frozen=True)
class DepositAccountAddresses:
    owner_wallet_address: str
    token_account_address: str


class SolanaTreasury:
    def __init__(
        self,
        *,
        rpc_url: str,
        usdc_mint: str,
        secret_tool_service: str,
        deposit_master_seed_secret_name: str,
        treasury_seed_secret_name: str,
    ) -> None:
        self._client = Client(rpc_url)
        self._usdc_mint = Pubkey.from_string(usdc_mint)
        self._secret_tool_service = secret_tool_service
        self._deposit_master_seed_secret_name = deposit_master_seed_secret_name
        self._treasury_keypair = Keypair.from_seed(
            self._load_seed_bytes(secret_name=treasury_seed_secret_name),
        )
        self._treasury_wallet = self._treasury_keypair.pubkey()

    def ensure_user_deposit_account(self, user_id: int) -> DepositAccountAddresses:
        deposit_owner = self._derive_user_owner_keypair(user_id).pubkey()
        token_account = get_associated_token_address(deposit_owner, self._usdc_mint)
        if self._client.get_account_info(token_account).value is None:
            self._send_instructions(
                [
                    create_associated_token_account(
                        payer=self._treasury_wallet,
                        owner=deposit_owner,
                        mint=self._usdc_mint,
                    )
                ]
            )

        return DepositAccountAddresses(
            owner_wallet_address=str(deposit_owner),
            token_account_address=str(token_account),
        )

    def reconcile_deposits(self, account_store: AccountStore) -> list[dict]:
        credited_deposits: list[dict] = []
        for deposit_account in account_store.list_deposit_accounts():
            token_account = Pubkey.from_string(deposit_account["token_account_address"])
            response = self._client.get_token_account_balance(token_account)
            onchain_balance_atomic = int(response.value.amount)
            observed_balance_atomic = deposit_account["observed_balance_atomic"]

            if onchain_balance_atomic < observed_balance_atomic:
                raise RuntimeError(
                    "Observed on-chain balance dropped below the last credited balance. "
                    f"Token account: {deposit_account['token_account_address']}"
                )

            delta_atomic = onchain_balance_atomic - observed_balance_atomic
            if delta_atomic == 0:
                continue

            credited_deposits.append(
                account_store.record_deposit_credit(
                    user_id=deposit_account["user_id"],
                    deposit_account_id=deposit_account["deposit_account_id"],
                    amount_atomic=delta_atomic,
                    observed_balance_after_atomic=onchain_balance_atomic,
                    credited_at=utc_now(),
                )
            )

        return credited_deposits

    def process_withdrawals(self, account_store: AccountStore, *, limit: int) -> list[dict]:
        processed_withdrawals: list[dict] = []

        for withdrawal in account_store.list_withdrawals_by_state(("broadcasted",), limit):
            if withdrawal["broadcast_signature"] is None:
                continue
            status_response = self._client.get_signature_statuses(
                [Signature.from_string(withdrawal["broadcast_signature"])]
            )
            status = status_response.value[0]
            if status is None:
                continue
            if status.err is not None:
                account_store.mark_withdrawal_failed(
                    withdrawal["id"],
                    f"On-chain withdrawal failed: {status.err}",
                    utc_now(),
                )
                processed_withdrawals.append(
                    {
                        "withdrawal_id": withdrawal["id"],
                        "state": "failed",
                        "reason": str(status.err),
                    }
                )
                continue

            account_store.mark_withdrawal_completed(withdrawal["id"], utc_now())
            processed_withdrawals.append({"withdrawal_id": withdrawal["id"], "state": "completed"})

        remaining_slots = max(limit - len(processed_withdrawals), 0)
        if remaining_slots == 0:
            return processed_withdrawals

        for withdrawal in account_store.list_withdrawals_by_state(("requested",), remaining_slots):
            try:
                destination_wallet = Pubkey.from_string(withdrawal["destination_wallet_address"])
                destination_token_account = get_associated_token_address(destination_wallet, self._usdc_mint)
                instructions = []
                if self._client.get_account_info(destination_token_account).value is None:
                    instructions.append(
                        create_associated_token_account(
                            payer=self._treasury_wallet,
                            owner=destination_wallet,
                            mint=self._usdc_mint,
                        )
                    )
                instructions.append(
                    transfer_checked(
                        TransferCheckedParams(
                            program_id=TOKEN_PROGRAM_ID,
                            source=get_associated_token_address(self._treasury_wallet, self._usdc_mint),
                            mint=self._usdc_mint,
                            dest=destination_token_account,
                            owner=self._treasury_wallet,
                            amount=withdrawal["amount_atomic"],
                            decimals=USDC_DECIMALS,
                            signers=[],
                        )
                    )
                )

                signature = self._send_instructions(instructions)
                account_store.mark_withdrawal_broadcasted(
                    withdrawal_id=withdrawal["id"],
                    destination_token_account_address=str(destination_token_account),
                    broadcast_signature=signature,
                    broadcast_at=utc_now(),
                )
                processed_withdrawals.append(
                    {
                        "withdrawal_id": withdrawal["id"],
                        "state": "broadcasted",
                        "broadcast_signature": signature,
                    }
                )
            except Exception as error:
                logger.exception("Failed to broadcast withdrawal {}", withdrawal["id"])
                account_store.mark_withdrawal_failed(withdrawal["id"], str(error), utc_now())
                processed_withdrawals.append(
                    {
                        "withdrawal_id": withdrawal["id"],
                        "state": "failed",
                        "reason": str(error),
                    }
                )

        return processed_withdrawals

    def _derive_user_owner_keypair(self, user_id: int) -> Keypair:
        master_seed = self._load_seed_bytes(secret_name=self._deposit_master_seed_secret_name)
        # A single master seed in secret-tool is enough to deterministically re-derive every
        # user deposit wallet without persisting any per-user private key material to disk.
        derived_seed = hmac.new(master_seed, str(user_id).encode("utf-8"), hashlib.sha256).digest()
        return Keypair.from_seed(derived_seed)

    def _load_seed_bytes(self, *, secret_name: str) -> bytes:
        result = subprocess.run(
            [
                "secret-tool",
                "lookup",
                "service",
                self._secret_tool_service,
                "name",
                secret_name,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        secret_text = result.stdout.strip()
        if len(secret_text) != 64:
            raise ValueError(
                f"Secret '{secret_name}' must be a 32-byte hex seed encoded as 64 hex characters."
            )
        return bytes.fromhex(secret_text)

    def _send_instructions(self, instructions: list) -> str:
        latest_blockhash = self._client.get_latest_blockhash().value
        transaction = Transaction.new_signed_with_payer(
            instructions,
            self._treasury_wallet,
            [self._treasury_keypair],
            latest_blockhash.blockhash,
        )
        response = self._client.send_transaction(transaction)
        return str(response.value)
