# Bags Rug Prediction Market MVP Spec

## Product Summary

The product is an offchain, real-money, USDC-settled prediction market for Bags tokens.

Each Bags token gets one binary market:

- `YES`: the token will be rugged within 90 days of launch.
- `NO`: the token will not be rugged within 90 days of launch.

The system is custodial for the MVP. Users deposit Solana USDC, trade on an internal ledger, and withdraw Solana USDC. Trading, positions, pricing, and settlement all happen offchain. Deposits and withdrawals happen onchain.

## Core Market Definition

### Market Question

Each market asks:

- `Will <TOKEN> be rugged by <LAUNCH_TIME + 90 days>?`

`<TOKEN>` is identified by mint address. The symbol and name are display fields only.

### Recommended Definition of "Rugged"

The definition should be objective, hard to dispute, and easy to explain.

Recommended user-facing definition:

- `Rugged` means the token stayed below minimum trading activity for two straight weeks.

Recommended formal rule for the MVP:

- Resolve `YES` if, before the 90 day deadline, both of these conditions are true for 14 consecutive UTC days.
- Combined 24h trading volume across tracked Solana pairs is below `$5,000`.
- Combined end-of-day liquidity across tracked Solana pairs is below `$10,000`.
- Resolve `NO` if this never happens by the 90 day deadline.

This rule is better than using price alone. Price can crash and recover. It is also better than using volume alone. Volume is easier to manipulate than a volume-plus-liquidity rule held over 14 straight days.

### Why This Rule Works

- It matches how traders already describe a dead token. Nobody is trading it and there is not enough liquidity left.
- It avoids intent-based language. The market settles on observed market conditions, not on whether a creator acted maliciously.
- It resolves early when the token is clearly dead, which makes the market more useful.
- It avoids the useless version of the question where everything eventually goes to zero on a long enough horizon.

### Resolution Data Source

The settlement rule needs one canonical data pipeline.

- Use Bags launch events to detect new tokens and launch timestamps.
- Use tracked Solana DEX pair data for rolling 24h volume and liquidity.
- Snapshot the relevant metrics every hour into an append-only settlement table.
- Compute daily UTC rollups from stored snapshots.
- Resolve from stored snapshots and rollups, not from a live API request at resolution time.

If the product later uses a different data provider, the settlement engine still resolves from the platform's stored snapshots for auditability.

### Supported Pair Logic

Some tokens will trade across multiple pools. The market should not resolve `YES` just because activity moved from one pool to another.

- Track all supported Solana DEX pairs for the token mint.
- Sum volume and liquidity across all tracked pairs.
- Freeze the tracked pair set once a market opens, except for admin-added pairs that are clearly the same token's live trading venue.

### Expiry and Early Resolution

- Market open time: as soon as launch time and token-pair mapping are confirmed.
- Market expiry time: launch time plus 90 days.
- Early `YES` resolution: as soon as day 14 of the rug condition completes.
- `NO` resolution: at expiry if the rug condition never completed.

## Product Scope

### Included In The MVP

- Custodial USDC deposits and withdrawals on Solana.
- Wallet-based login.
- One market per token.
- Offchain pricing and trade matching against a platform-run market maker.
- Live market pages with price, depth, charts, and settlement metrics.
- Portfolio and position management.
- Resolution engine and settlement ledger.
- Admin tools for treasury operations, market operations, and surveillance.
- Basic notifications for deposits, withdrawals, fills, and market resolution.

### Excluded From The MVP

- User-to-user order book matching.
- Margin, leverage, borrowing, and shorting beyond the binary share model.
- Fiat rails.
- Cross-chain deposits.
- Social features, comments, and creator profiles.
- Copy trading and advanced APIs for external market makers.

## Trading Model

### Market Structure

Each market has two outcomes:

- `YES`
- `NO`

At resolution:

- One winning share pays `1.00 USDC`.
- One losing share pays `0.00 USDC`.

### Pricing Model

The simplest useful MVP is a platform-run binary market maker, not a user order book.

Recommended approach:

- Use a bounded-loss binary market maker such as LMSR.
- Seed each market with a small liquidity parameter and strict user position limits.
- Let users buy or sell `YES` and `NO` continuously against the market maker.

Reasons:

- One market per token creates a very long tail of illiquid markets.
- A user order book will be empty across most tokens.
- A market maker gives immediate quotes and clean UX from day one.

### Trade Inputs

The user should be able to trade in either of these ways:

- Enter a USDC amount to spend.
- Enter the number of shares to buy or sell.

The trade ticket should always show:

- current probability
- estimated average fill price
- maximum loss
- payout if `YES`
- payout if `NO`
- fee

### Position Limits

The MVP needs hard caps.

- Per-market max net exposure per user.
- Per-user platform-wide max exposure.
- Smaller caps for newly created markets.
- Higher caps only after liquidity and volume increase.

These caps reduce blowups from house market making and make manipulation more expensive.

### Fees

Keep the fee model simple.

Recommended MVP fee model:

- trading fee on each executed trade
- no maker/taker distinction
- fixed withdrawal fee or pass-through network fee

Do not hide platform economics inside the resolution rule.

## User Accounts And Identity

### Account Model

Each account should have:

- one internal user id
- one or more linked Solana wallets
- one USDC cash balance
- one set of open positions
- one ledger history

### Login

The cleanest MVP login flow is wallet signature plus an email for notifications and recovery.

Flow:

- User connects a Solana wallet.
- Backend returns a one-time challenge message.
- User signs the challenge.
- Backend verifies the signature and creates or loads the account.
- Backend issues a session token.

Optional hardening for the MVP:

- TOTP for withdrawals.
- Email verification before first withdrawal.

## Deposit Flow

### Custody Model

The product is custodial in the MVP.

- Users hold an internal platform balance.
- The platform controls treasury wallets.
- Deposits and withdrawals move USDC between the user and the platform.

### Wallet Layout

Use a simple hot/cold split.

- Hot wallet for routine withdrawals.
- Cold wallet for treasury reserves.
- Unique deposit address or unique deposit token account per user.
- Sweeper job to consolidate deposits into treasury.

Private keys belong in a secure secret manager or HSM-backed system, not in app config.

### Deposit Flow

1. The user opens the deposit screen.
2. The frontend requests the user's deposit address.
3. The backend returns the user's dedicated Solana USDC deposit address.
4. The user sends USDC to that address.
5. A chain watcher detects the transfer and validates token mint, recipient, and amount.
6. After the required confirmation threshold, the backend creates a `deposit_pending` ledger entry.
7. The sweeper consolidates the funds into treasury.
8. Once the transfer is reconciled, the backend marks the deposit `credited` and increases the user's available USDC balance.
9. The user receives an in-app and email notification.

### Deposit Rules

- Only native Solana USDC is accepted in the MVP.
- Unsupported tokens are not auto-credited.
- Deposits below a minimum threshold stay pending for manual review or are rejected by policy.
- The user must see deposit status: `awaiting funds`, `pending confirmations`, `credited`, `failed`, `manual review`.

## Withdrawal Flow

### Withdrawal UX

The user should be able to withdraw available USDC to any linked or manually entered Solana address.

The screen should show:

- available balance
- pending balance
- estimated network fee
- destination address
- withdrawal status history

### Withdrawal Flow

1. The user opens the withdrawal screen.
2. The frontend fetches the available balance and recent withdrawal history.
3. The user enters amount and destination address.
4. The frontend asks for wallet re-sign and, if enabled, TOTP.
5. The backend validates balance, limits, cooldown rules, and destination format.
6. The backend locks the requested amount in a `withdrawal_pending` ledger state.
7. The treasury service creates and signs the Solana USDC transfer from the hot wallet.
8. The backend stores the transaction hash and marks the withdrawal `broadcast`.
9. After chain confirmation and reconciliation, the backend marks the withdrawal `completed`.
10. If the chain transfer fails before broadcast, the backend unlocks the funds and marks the withdrawal `failed`.

### Withdrawal Controls

The MVP needs basic treasury risk controls.

- Cooldown after first deposit before the first withdrawal.
- Manual review for large withdrawals.
- Manual review for first withdrawal to a new address.
- Daily per-user withdrawal limit.
- Hot wallet balance alarms.

## Frontend Spec

### Core Pages

#### Landing And Discovery

The landing page should answer one question fast: what tokens does the market think are most likely to rug soon.

Include:

- list of active markets
- sortable columns for token, rug probability, time to expiry, liquidity, volume, and recent price change
- filters for `new`, `trending`, `expiring soon`, `high probability`, and `resolved`
- search by token symbol, name, or mint address

#### Market Detail Page

This is the main trading page.

Include:

- token name, symbol, mint, and launch time
- the binary market question
- countdown to expiry
- current `YES` probability
- buy and sell ticket for `YES` and `NO`
- user's current position and average entry
- underlying token charts for price, volume, and liquidity
- rug-condition tracker that shows the current consecutive-day count
- activity feed for trades, market status changes, and resolution events
- settlement rules panel written in plain English

#### Portfolio

Include:

- total account balance
- available USDC
- locked USDC
- open positions
- unrealized PnL
- realized PnL
- resolved market payouts
- trade history

#### Deposit And Withdraw

Include:

- deposit address
- QR code
- current deposit status
- withdrawal form
- withdrawal history

#### Account And Settings

Include:

- linked wallets
- email
- notification preferences
- security settings
- TOTP setup

### Frontend Realtime Features

The frontend should subscribe to live updates for:

- market prices
- trade executions
- order ticket quotes
- portfolio balance updates
- deposit and withdrawal state changes
- market resolution state changes

WebSocket or server-sent events are enough for the MVP.

### Frontend States

Every page needs explicit empty, loading, and error states.

Important examples:

- wallet not connected
- wallet connected but no deposit yet
- deposit detected but not credited
- market halted
- market resolved
- data feed stale

## Backend Spec

### Core Services

The backend should be split into a few focused services or modules.

#### Auth Service

Responsibilities:

- wallet challenge generation
- signature verification
- session issuance
- account lookup

#### Wallet And Treasury Service

Responsibilities:

- user deposit address management
- chain watchers
- deposit reconciliation
- withdrawal creation and broadcast
- hot/cold wallet balance management

#### Ledger Service

Responsibilities:

- cash balance accounting
- reserved balance accounting
- position settlement accounting
- immutable ledger entries for every money movement

#### Market Catalog Service

Responsibilities:

- token ingestion
- market creation
- market metadata
- market lifecycle status

#### Trading Engine

Responsibilities:

- quote generation
- fee calculation
- trade execution
- position updates
- market maker state updates
- risk limits

#### Resolution Service

Responsibilities:

- snapshot ingestion
- daily rollups
- rug-condition evaluation
- market resolution
- payout generation

#### Admin Service

Responsibilities:

- market halt and resume
- market void
- manual pair mapping
- restricted-wallet enforcement
- treasury review queue
- audit log browsing

### Recommended MVP Architecture

- Frontend: Next.js or another React SPA with server-rendered market pages.
- Backend API: Python FastAPI or a comparable HTTP framework.
- Database: Postgres.
- Background jobs: worker queue for chain watchers, ingestion, settlement, and notifications.
- Cache and pub/sub: Redis.
- Solana integration: dedicated RPC provider.

This stack is not the product. It is the shortest path to shipping the product.

## Data Model

### Core Tables

#### Users

- `id`
- `email`
- `status`
- `created_at`

#### Wallets

- `id`
- `user_id`
- `chain`
- `address`
- `verified_at`

#### Markets

- `id`
- `token_mint`
- `token_symbol`
- `token_name`
- `launch_time`
- `expiry_time`
- `status`
- `rug_probability`
- `resolution`
- `resolved_at`

#### Market Pair Mappings

- `id`
- `market_id`
- `pair_address`
- `source`
- `status`

#### Market Metric Snapshots

- `id`
- `market_id`
- `captured_at`
- `volume_24h_usd`
- `liquidity_usd`
- `price_usd`

#### Accounts

- `user_id`
- `available_usdc`
- `reserved_usdc`
- `updated_at`

#### Positions

- `id`
- `user_id`
- `market_id`
- `outcome`
- `shares`
- `average_entry_price`
- `realized_pnl`

#### Trades

- `id`
- `user_id`
- `market_id`
- `side`
- `outcome`
- `shares`
- `price`
- `fee_usdc`
- `executed_at`

#### Ledger Entries

- `id`
- `user_id`
- `entry_type`
- `asset`
- `amount`
- `reference_type`
- `reference_id`
- `created_at`

#### Deposits

- `id`
- `user_id`
- `wallet_address`
- `tx_hash`
- `amount`
- `status`
- `created_at`

#### Withdrawals

- `id`
- `user_id`
- `destination_address`
- `tx_hash`
- `amount`
- `fee`
- `status`
- `created_at`

#### Admin Actions

- `id`
- `admin_user_id`
- `action_type`
- `target_type`
- `target_id`
- `notes`
- `created_at`

### Ledger Rule

Do not derive balances from mutable position state. Derive balances from immutable ledger entries and reconcile them continuously.

## Market Lifecycle

### States

- `draft`
- `open`
- `halted`
- `resolved_yes`
- `resolved_no`
- `void`

### Lifecycle Rules

- `draft` exists only briefly while token launch and pair mapping are being confirmed.
- `open` allows trading.
- `halted` blocks new trades and withdrawals of unsettled proceeds tied to that market.
- `resolved_yes` and `resolved_no` trigger payout settlement.
- `void` unwinds the market and returns users to flat PnL for that market.

### Void Rules

The platform needs a clear void policy even in the MVP.

Valid void reasons:

- broken token-to-pair mapping
- missing settlement data for a material portion of the market's life
- corrupted market creation event

Void handling:

- reverse all trade effects for that market
- refund trading fees for that market
- settle user PnL to zero

## Resolution Engine

### Hourly Snapshot Job

Every hour:

- fetch tracked pair metrics
- store market snapshot
- mark stale markets if data is missing

### Daily Rollup Job

At each UTC day boundary:

- calculate combined 24h volume
- calculate end-of-day combined liquidity
- evaluate whether the day satisfies the rug condition
- update each market's consecutive-day rug counter

### Resolution Job

After each daily rollup:

- resolve `YES` if the rug counter reaches 14 before expiry
- resolve `NO` at expiry if the rug counter is below 14
- generate payout ledger entries
- notify users

### User-Facing Resolution Display

Each market page should show:

- current consecutive-day rug counter
- latest daily volume
- latest daily liquidity
- the exact UTC time the market will resolve if the current streak continues

This makes the resolution rule legible while the market is live.

## Market Integrity And Abuse Controls

### Restricted Traders

The platform should block clearly conflicted parties from their own token market.

Restricted traders:

- token creator wallets
- wallets that received creator allocation
- platform-controlled treasury wallets
- wallets explicitly linked by admin review

### Trade Surveillance

The MVP needs lightweight surveillance.

- flag rapid back-and-forth trading by the same wallet cluster
- flag coordinated deposits followed by one-sided trading in a single market
- flag repeated interactions with markets tied to a linked token creator
- flag unusual trading before resolution

### Data Quality Controls

- alert if metric snapshots stop arriving
- alert if pair liquidity drops to zero abruptly across all pairs
- alert if tracked pair set changes unexpectedly

## Admin Surface

### Treasury Dashboard

Include:

- hot wallet balance
- cold wallet balance
- pending deposits
- pending withdrawals
- failed chain transactions
- manual review queue

### Market Operations Dashboard

Include:

- market status overrides
- manual pair mapping
- market search by mint
- resolution preview
- stale data alerts

### Risk Dashboard

Include:

- largest user exposures
- largest market maker exposures
- restricted wallet hits
- suspicious trading alerts

### Audit Log

Every admin action must be logged with:

- actor
- timestamp
- target
- reason

## Notifications

The MVP should send:

- deposit detected
- deposit credited
- withdrawal submitted
- withdrawal completed
- trade filled
- market halted
- market resolved
- payout credited

In-app notifications are enough for the MVP. Email should cover deposits, withdrawals, and market resolution.

## Useful MVP Defaults

These defaults keep the first version manageable.

- Market duration: 90 days from token launch.
- Rug streak length: 14 consecutive UTC days.
- Daily volume threshold: `$5,000`.
- Daily liquidity threshold: `$10,000`.
- Asset supported: Solana USDC only.
- Trade engine: platform-run binary market maker.
- Settlement source: platform-stored hourly snapshots from tracked market data.

The threshold values require historical backtesting before launch. The structure does not.

## Delivery Plan

### Phase One

- Finalize the rug rule and data source.
- Build wallet login, account model, and internal ledger.
- Build deposit address generation, chain watcher, deposit crediting, and withdrawal broadcasting.

### Phase Two

- Build token ingestion and market creation.
- Build market pages, live quotes, and trading execution.
- Build portfolio, deposit, and withdrawal screens.

### Phase Three

- Build snapshot ingestion, daily rollups, and resolution engine.
- Build payout ledger logic.
- Build notifications and admin dashboards.

### Phase Four

- Backtest thresholds on historical Bags tokens.
- Tune limits, fees, and market maker parameters.
- Run a closed beta with low limits and manual monitoring.

## Non-Obvious Product Decisions

### Why Not Use Price As The Rug Rule

Price is noisy and reflexive. A token can crash hard and still remain actively traded. The market question is about whether the token is effectively dead inside a useful window, not whether it experienced a drawdown.

### Why Not Use A User Order Book

One market per token creates too many markets for an order book to stay alive. The MVP needs instant quotes and bounded operator risk more than it needs a pure peer-to-peer matching model.

### Why Custodial First

An offchain market with onchain deposits and withdrawals needs a real internal ledger. A custodial MVP is the shortest path to consistent balances, fast fills, and clean settlement.

### Why Use Mint Address As The Canonical Identifier

Symbols are ambiguous and can change. The mint address is the identity that the pricing engine, settlement engine, and custody logic can trust.

## Open Questions

- Should trading open immediately at launch, or only after the token has a minimum initial liquidity threshold?
- Should every market start with the same market maker liquidity parameter, or should seeding vary by early token activity?
- Should first-time withdrawals require only wallet re-sign, or wallet re-sign plus TOTP?
- Should the platform support only the latest Bags tokens, or also backfill older tokens with fresh 90 day markets from listing time?
