import sqlite3

from nukefm.accounts import AccountStore


def test_authenticate_api_key_is_read_only_under_write_lock(tmp_path):
    database_path = tmp_path / "catalog.sqlite3"
    account_store = AccountStore(database_path)
    account_store.initialize()
    user = account_store.ensure_user("wallet-1")
    api_key = account_store.issue_api_key(user["id"])["api_key"]

    lock_connection = sqlite3.connect(database_path)
    try:
        lock_connection.execute("BEGIN IMMEDIATE")

        authenticated_user = account_store.authenticate_api_key(api_key)
    finally:
        lock_connection.rollback()
        lock_connection.close()

    assert authenticated_user is not None
    assert authenticated_user.wallet_address == "wallet-1"
