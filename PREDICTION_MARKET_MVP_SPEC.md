# Bags Rug Prediction Market MVP Spec

## Product

An offchain, real-money prediction market for Bags tokens, settled in Solana USDC.

Each Bags token gets one binary market:

- `YES`: the token will be rugged within 90 days of launch.
- `NO`: the token will not be rugged within 90 days of launch.

The MVP is custodial. Deposits and withdrawals happen onchain. Trading, balances, positions, and settlement happen on an internal ledger.

## Market Definition

### Market Question

- `Will <TOKEN> be rugged by <LAUNCH_TIME + 90 days>?`

The canonical identifier is the token mint address. Symbol and name are display metadata.

### Definition Of Rugged

Use a definition based on price collapse from post-launch ATH.

User-facing definition:

- `Rugged` means the token fell more than 95% from its all-time high within 90 days of launch.

Formal rule:

- Define `reference price` as the liquidity-weighted average token price across tracked Solana pairs at each hourly snapshot.
- Define `ATH` as the highest recorded reference price between market open and expiry.
- Resolve `YES` if, at any later hourly snapshot before expiry, the reference price is at or below `5%` of `ATH`.
- Resolve `NO` at expiry otherwise.

Why this rule:

- It is easy to explain.
- It matches how traders talk about a token being destroyed.
- It resolves inside a useful time window instead of asking a question that becomes trivial over a long horizon.
- It uses hourly aggregated prices rather than raw wick highs, which makes settlement less sensitive to one bad trade.

### Pair Tracking

- Track all supported Solana trading pairs for the token.
- Compute the reference price from the tracked pair set, not from a single pool.
- Resolve from the tracked set, not from a single pool.

### Resolution Source

- Detect token launches from the Bags launch feed.
- Ingest token price and pair-liquidity data on a fixed schedule.
- Store hourly snapshots in an append-only settlement table.
- Compute the reference price and running ATH from stored snapshots.
- Resolve markets from stored data, not a live API call at resolution time.

## Trading Model

The MVP should use a platform-run binary quote engine rather than a user order book.

Reason:

- One market per token creates too many thin markets for an order book to work well in v1.

Trading behavior:

- Users can buy or sell `YES` and `NO`.
- Winning shares settle to `1.00 USDC`.
- Losing shares settle to `0.00 USDC`.
- The trade ticket shows quoted price, shares, total cost or proceeds, fee, and max loss.

The spec does not require fixed position caps or other arbitrary exposure rules. Those belong in operations policy if they are needed later.

## User Flows

### Signup And Login

- User connects a Solana wallet.
- Backend issues a one-time challenge.
- User signs the challenge.
- Backend verifies the signature and creates or loads the account.
- Backend returns a session.

### Deposit

- User opens the deposit screen.
- Frontend requests the user's USDC deposit address.
- Backend returns the user's dedicated Solana USDC deposit address.
- User sends USDC on Solana.
- Chain watcher detects the transfer and verifies mint, destination, and amount.
- After confirmation, the backend credits the user's internal USDC balance.
- The user sees deposit status updates until the balance is available.

### Withdraw

- User opens the withdrawal screen.
- Frontend fetches available balance and withdrawal history.
- User enters amount and destination address.
- Backend validates the request and reserves the amount.
- Treasury service sends Solana USDC from the platform wallet.
- Backend records the transaction hash and final completion state.
- The user sees withdrawal status updates until completion or failure.

### Trade

- User opens a market page.
- Frontend requests a live quote for the selected side and size.
- User confirms the trade.
- Backend re-checks the quote, executes the fill, updates positions and cash balances, and records the trade in the ledger.
- Frontend refreshes portfolio state and market state.

### Settlement

- Resolution job evaluates hourly snapshots.
- A market resolves `YES` as soon as post-ATH drawdown reaches at least 95%.
- A market resolves `NO` at expiry if that never happens.
- Backend generates payout ledger entries.
- User balance and portfolio update immediately after settlement.

## Frontend

### Core Pages

#### Market List

The landing view should make discovery fast.

Include:

- active markets
- rug probability
- current drawdown from ATH
- expiry countdown
- token search
- filters for `new`, `active`, `expiring`, and `resolved`

#### Market Detail

This is the main trading page.

Include:

- token name, symbol, mint, and launch time
- market question
- expiry countdown
- current `YES` probability
- buy and sell ticket for `YES` and `NO`
- user's current position
- recent market activity
- token price chart
- ATH and ATH timestamp
- current drawdown from ATH
- plain-English settlement rule

#### Portfolio

Include:

- available USDC
- open positions
- realized and unrealized PnL
- resolved payouts
- trade history

#### Deposit And Withdraw

Include:

- deposit address and QR code
- deposit history and status
- withdrawal form
- withdrawal history and status

#### Account

Include:

- linked wallets
- email
- notification settings
- session and security settings

### Realtime Updates

The frontend should receive live updates for:

- quotes
- trades
- portfolio balances
- deposits
- withdrawals
- market resolution

WebSocket or server-sent events are sufficient for the MVP.

### Required States

Every page should have clear loading, empty, and error states.

Important cases:

- wallet not connected
- no deposit yet
- deposit pending
- market halted
- market resolved
- stale market data

## Backend

### Core Modules

#### Auth

- wallet challenge generation
- signature verification
- session issuance

#### Treasury

- deposit address management
- chain watchers
- deposit reconciliation
- withdrawal creation and broadcast
- treasury balance tracking

#### Ledger

- cash balance accounting
- reserved balance accounting
- trade settlement accounting
- immutable money-movement history

#### Market Catalog

- token ingestion
- market creation
- token metadata
- market status

#### Trading Engine

- quote generation
- fee calculation
- trade execution
- position updates
- market pricing state

#### Resolution

- metric snapshot ingestion
- reference price calculation
- ATH tracking
- drawdown evaluation
- market resolution
- payout generation

#### Admin

- halt and resume markets
- void markets
- manage pair mappings
- inspect treasury flows
- inspect settlement data
- review audit history

### Minimal Data Model

The backend needs, at minimum:

- users
- linked wallets
- accounts and balances
- markets
- tracked pairs per market
- hourly market snapshots
- positions
- trades
- ledger entries
- deposits
- withdrawals
- admin actions

Balances should be derived from ledger entries, not from mutable cached totals alone.

## Market Lifecycle

States:

- `draft`
- `open`
- `halted`
- `resolved_yes`
- `resolved_no`
- `void`

Rules:

- `draft` exists while launch and pair mapping are being confirmed.
- `open` allows trading.
- `halted` pauses trading.
- `resolved_yes` and `resolved_no` trigger payouts.
- `void` unwinds the market because the market definition or settlement data was materially broken.

Valid reasons to void:

- incorrect token-to-pair mapping
- materially incomplete settlement data
- invalid market creation event

## Admin And Operations

The MVP still needs a small but real admin surface.

### Treasury

- view balances
- inspect pending deposits and withdrawals
- inspect failed chain transactions

### Market Operations

- search markets by mint or symbol
- inspect tracked pairs
- preview settlement state
- halt, resume, or void a market

### Audit

- log every admin action with actor, timestamp, target, and reason

### Notifications

Send user notifications for:

- deposit detected
- deposit credited
- withdrawal submitted
- withdrawal completed or failed
- trade filled
- market halted
- market resolved
- payout credited

## Out Of Scope

The MVP does not need:

- peer-to-peer order book matching
- margin or leverage
- fiat rails
- non-USDC settlement
- cross-chain custody
- social features

## Delivery Order

### First

- wallet login
- internal ledger
- deposits
- withdrawals

### Next

- token ingestion
- market creation
- market list and market detail pages
- quote engine and trade execution
- portfolio

### Then

- metric ingestion
- reference price calculation and ATH tracking
- resolution engine
- payout settlement
- admin surface
- notifications

## Open Questions

- Should trading open immediately at token launch, or only after pair discovery is confirmed?
- Should `rugged` remain the public term, or should the product use a more literal label such as `down 95% from ATH` while keeping the same rule?
- Should the platform launch with only new Bags tokens, or also backfill older tokens into fresh 90 day markets?
