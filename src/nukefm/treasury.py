from __future__ import annotations

import hashlib
import hmac
import subprocess
from dataclasses import dataclass
from time import sleep

import httpx
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
        self._treasury_token_account = get_associated_token_address(self._treasury_wallet, self._usdc_mint)

    def ensure_user_deposit_account(self, user_id: int) -> DepositAccountAddresses:
        return self._ensure_derived_token_account(
            owner_keypair=self._derive_user_owner_keypair(user_id),
        )

    def ensure_market_liquidity_account(self, market_id: int) -> DepositAccountAddresses:
        return self._ensure_derived_token_account(
            owner_keypair=self._derive_market_owner_keypair(market_id),
        )

    def reconcile_deposits(self, account_store: AccountStore) -> list[dict]:
        credited_deposits: list[dict] = []
        for deposit_account in account_store.list_deposit_accounts():
            onchain_balance_atomic = self.get_token_account_balance(deposit_account["token_account_address"])
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

    def reconcile_market_liquidity(self, market_store) -> list[dict]:
        credited_deposits: list[dict] = []
        for deposit_account in market_store.list_market_liquidity_accounts():
            onchain_balance_atomic = self.get_token_account_balance(deposit_account["token_account_address"])
            observed_balance_atomic = deposit_account["observed_balance_atomic"]
            if onchain_balance_atomic < observed_balance_atomic:
                raise RuntimeError(
                    "Observed on-chain market liquidity balance dropped below the last credited balance. "
                    f"Token account: {deposit_account['token_account_address']}"
                )
            delta_atomic = onchain_balance_atomic - observed_balance_atomic
            if delta_atomic == 0:
                continue
            credited_deposits.append(
                market_store.record_market_liquidity_credit(
                    market_id=deposit_account["market_id"],
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

                signature = self._send_instructions(instructions, signers=[self._treasury_keypair])
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

    def sweep_market_revenue(self, market_store, *, limit: int) -> list[dict]:
        processed_sweeps: list[dict] = []
        self._ensure_associated_token_account(owner=self._treasury_wallet)

        for sweep in market_store.list_pending_revenue_sweeps(limit=limit):
            try:
                source_token_account = sweep["source_token_account_address"]
                if source_token_account is None:
                    market_store.mark_revenue_sweep_completed(
                        market_id=sweep["market_id"],
                        destination_token_account_address=str(self._treasury_token_account),
                        onchain_amount_atomic=0,
                        broadcast_signature="recorded-only",
                        completed_at=utc_now(),
                    )
                    processed_sweeps.append({"market_id": sweep["market_id"], "state": "completed", "onchain_amount_usdc": "0"})
                    continue

                onchain_amount_atomic = self.get_token_account_balance(source_token_account)
                if onchain_amount_atomic == 0:
                    market_store.mark_revenue_sweep_completed(
                        market_id=sweep["market_id"],
                        destination_token_account_address=str(self._treasury_token_account),
                        onchain_amount_atomic=0,
                        broadcast_signature="empty-balance",
                        completed_at=utc_now(),
                    )
                    processed_sweeps.append({"market_id": sweep["market_id"], "state": "completed", "onchain_amount_usdc": "0"})
                    continue

                market_owner_keypair = self._derive_market_owner_keypair(sweep["market_id"])
                signature = self._send_instructions(
                    [
                        transfer_checked(
                            TransferCheckedParams(
                                program_id=TOKEN_PROGRAM_ID,
                                source=Pubkey.from_string(source_token_account),
                                mint=self._usdc_mint,
                                dest=self._treasury_token_account,
                                owner=market_owner_keypair.pubkey(),
                                amount=onchain_amount_atomic,
                                decimals=USDC_DECIMALS,
                                signers=[],
                            )
                        )
                    ],
                    signers=[self._treasury_keypair, market_owner_keypair],
                )
                market_store.mark_revenue_sweep_completed(
                    market_id=sweep["market_id"],
                    destination_token_account_address=str(self._treasury_token_account),
                    onchain_amount_atomic=onchain_amount_atomic,
                    broadcast_signature=signature,
                    completed_at=utc_now(),
                )
                processed_sweeps.append(
                    {
                        "market_id": sweep["market_id"],
                        "state": "completed",
                        "broadcast_signature": signature,
                    }
                )
            except Exception as error:
                logger.exception("Failed to sweep resolved market {}", sweep["market_id"])
                market_store.mark_revenue_sweep_failed(
                    market_id=sweep["market_id"],
                    failure_reason=str(error),
                    failed_at=utc_now(),
                )
                processed_sweeps.append({"market_id": sweep["market_id"], "state": "failed", "reason": str(error)})

        return processed_sweeps

    def get_token_account_balance(self, token_account_address: str) -> int:
        response = self._rpc_call(
            lambda: self._client.get_token_account_balance(Pubkey.from_string(token_account_address)),
            description=f"get token account balance for {token_account_address}",
        )
        return int(response.value.amount)

    def _derive_user_owner_keypair(self, user_id: int) -> Keypair:
        return self._derive_owner_keypair(f"user:{user_id}")

    def _derive_market_owner_keypair(self, market_id: int) -> Keypair:
        return self._derive_owner_keypair(f"market:{market_id}")

    def _derive_owner_keypair(self, domain: str) -> Keypair:
        master_seed = self._load_seed_bytes(secret_name=self._deposit_master_seed_secret_name)
        # One master seed is enough for both user funding accounts and public market liquidity
        # accounts as long as the HMAC input is domain-separated and deterministic.
        derived_seed = hmac.new(master_seed, domain.encode("utf-8"), hashlib.sha256).digest()
        return Keypair.from_seed(derived_seed)

    def _ensure_derived_token_account(self, *, owner_keypair: Keypair) -> DepositAccountAddresses:
        owner_wallet = owner_keypair.pubkey()
        token_account = get_associated_token_address(owner_wallet, self._usdc_mint)
        self._ensure_associated_token_account(owner=owner_wallet)
        return DepositAccountAddresses(
            owner_wallet_address=str(owner_wallet),
            token_account_address=str(token_account),
        )

    def _ensure_associated_token_account(self, *, owner: Pubkey) -> None:
        token_account = get_associated_token_address(owner, self._usdc_mint)
        if self._rpc_call(
            lambda: self._client.get_account_info(token_account),
            description=f"get account info for {token_account}",
        ).value is not None:
            return
        self._send_instructions(
            [
                create_associated_token_account(
                    payer=self._treasury_wallet,
                    owner=owner,
                    mint=self._usdc_mint,
                )
            ],
            signers=[self._treasury_keypair],
        )

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

    def _send_instructions(self, instructions: list, *, signers: list[Keypair]) -> str:
        latest_blockhash = self._rpc_call(
            self._client.get_latest_blockhash,
            description="get latest blockhash",
        ).value
        transaction = Transaction.new_signed_with_payer(
            instructions,
            self._treasury_wallet,
            signers,
            latest_blockhash.blockhash,
        )
        response = self._rpc_call(
            lambda: self._client.send_transaction(transaction),
            description="send Solana transaction",
        )
        return str(response.value)

    def _rpc_call(self, operation, *, description: str):
        for attempt in range(5):
            try:
                return operation()
            except Exception as error:
                if not self._is_retryable_rpc_error(error) or attempt == 4:
                    raise

                backoff_seconds = 1 + attempt * 2
                logger.warning(
                    "Retrying Solana RPC call after rate limit: {} (attempt {} of 5, sleeping {}s)",
                    description,
                    attempt + 1,
                    backoff_seconds,
                )
                sleep(backoff_seconds)

        raise RuntimeError(f"Solana RPC retry loop exited unexpectedly for {description}.")

    def _is_retryable_rpc_error(self, error: Exception) -> bool:
        current_error: Exception | None = error
        while current_error is not None:
            if isinstance(current_error, httpx.HTTPStatusError):
                return current_error.response.status_code == 429
            current_error = current_error.__cause__
        return False
