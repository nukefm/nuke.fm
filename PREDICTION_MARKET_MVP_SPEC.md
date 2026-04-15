# nuke.fm Prediction Market MVP Spec

## Product

An offchain, real-money prediction market for Bags tokens, settled in Solana USDC.

The web frontend is read-only. It exists to publish market state, not to trade. All trading, balances, deposits, withdrawals, positions, and portfolio access happen through the API.

Each token has a token page and a rolling prediction market series. Each active market uses one backend-only XYK AMM between `YES` and `NO`.

## Market Series

### Market Question

- `Will <TOKEN> nuke by <MARKET_START + 90 days>?`

Use the public term `nuke`, not `rug`.

The canonical token identifier is the mint address. Symbol and name are display metadata.

### Rolling Market Model

- Every token has one current market record.
- New tokens and historical tokens are treated the same way.
- The current market starts in `awaiting_liquidity`.
- A market becomes `open` as soon as its first liquidity deposit is credited.
- A market resolves to `YES` or `NO`.
- As soon as a market resolves, the next market for that same token is created immediately in `awaiting_liquidity`.

This creates a continuous forward-looking market series for each token. There is always a current forward market record for the token, even if that current market is still waiting for liquidity. A token can have many past markets even if it already nuked in an earlier one.

### Market Start And Expiry

- `MARKET_START` is the timestamp when the first liquidity deposit for that market is credited.
- `EXPIRY` is `MARKET_START + 90 days`.

This rule is required because the product supports both newly launched tokens and older tokens that are backfilled later.

## Nuke Definition

### User-Facing Rule

- `Nuke` means the token fell more than 95% from that market's own ATH within the market's 90 day window.

### Formal Rule

- Define `reference price` as the liquidity-weighted average token price across tracked Solana pairs at each hourly snapshot.
- Define `ATH` as the highest recorded reference price between `MARKET_START` and `EXPIRY`.
- Define `threshold price` as `5% of ATH`.
- Resolve `YES` if, at any later hourly snapshot before `EXPIRY`, the reference price is at or below the threshold price.
- Resolve `NO` at `EXPIRY` otherwise.

### Important Scope Rule

ATH is tracked per market, not per token lifetime.

An earlier nuke does not poison later markets. If a token dies, comes back, and later pumps again, the next market tracks its own ATH from its own start.

### Resolution Source

- Detect tokens from the Bags launch feed and token catalog.
- Track all supported Solana pairs for the token.
- Store hourly pair snapshots in an append-only settlement table.
- Compute the hourly reference price from the tracked pair set.
- Resolve from stored snapshots, not from a live API call at resolution time.

## AMM Model

### Pool Structure

Each active market has one offchain XYK pool with:

- `YES reserve`
- `NO reserve`
- invariant `YES_reserve * NO_reserve = k`

There is no order book.

### Price Display

The pool is internally `YES <> NO`, but the frontend should display both prices in USDC and as probabilities.

Use:

- `YES price in USDC = NO_reserve / (YES_reserve + NO_reserve)`
- `NO price in USDC = YES_reserve / (YES_reserve + NO_reserve)`

These prices sum to `1.00` and can be displayed as probabilities directly.

### Trading

- Traders buy or sell `YES` and `NO` against the XYK pool through the API only.
- The API should expose quote and execution endpoints.
- Winning shares settle to `1.00 USDC`.
- Losing shares settle to `0.00 USDC`.

### Liquidity Deposits

- Any user can deposit liquidity into any market.
- Each market has its own public Solana USDC liquidity deposit address.
- Liquidity deposits are one-way only.
- Liquidity cannot be withdrawn.
- Liquidity deposits do not mint LP shares and do not accrue LP fee claims.

When a liquidity deposit is credited:

- the backend mints complete sets internally
- the deposit amount becomes equal additions to the market's `YES` and `NO` reserves
- the pool price stays unchanged at the moment of deposit

### Liquidity At Resolution

- After a market resolves and all user payouts are accounted for, any remaining market liquidity is swept to the platform revenue address.
- The next market for that token is still created immediately, but it starts in `awaiting_liquidity` and needs a fresh liquidity deposit to open.

## Read-Only Frontend

The frontend should not allow:

- wallet connection
- trading
- account funding
- withdrawals
- portfolio access

### Market List

The main page should show:

- token name and symbol
- token mint
- current market state
- `YES` price
- `NO` price
- current reference price
- current ATH
- current drawdown from ATH
- current threshold price
- time to expiry for open markets
- public liquidity deposit address for the current market

The market list should include tokens whose current market is still `awaiting_liquidity`.

### Token Detail Page

Each token page should show:

- the current market question
- current market state
- current `YES` and `NO` prices
- current reference price
- ATH and ATH timestamp
- current drawdown from ATH
- current threshold price
- market start and expiry
- current market liquidity deposit address
- recent market activity summary
- list of past resolved markets for the same token, including outcome and dates

If the current market is still `awaiting_liquidity`, the page should still be visible and should show the deposit address needed to initialize it.

### Realtime Updates

The read-only frontend should receive live updates for:

- current market prices
- current ATH
- current drawdown
- current threshold price
- market state changes
- market resolution

## API

The API is the primary product surface for trading bots and agent traders.

### Public API

The public API should provide:

- token list
- token detail by mint
- current market by token
- past resolved markets by token
- current `YES` and `NO` prices
- current reference price
- ATH
- ATH timestamp
- current drawdown
- current threshold price
- market start and expiry
- current market state
- market liquidity deposit address

### Private API

The private API should provide:

- API key bootstrap
- account deposit address
- account balance
- open positions
- trade history
- current quotes
- trade execution
- withdrawals
- account deposit history

Portfolio and account access are API-only in the MVP.

### Auth

Use API keys for private access.

Recommended bootstrap flow:

- client requests a one-time challenge
- client signs it with the wallet
- backend verifies the signature
- backend issues an API key

The frontend does not need to support this flow because the frontend is read-only.

## User Flows

### Account Funding

- User requests a personal Solana USDC deposit address from the private API.
- User sends USDC.
- Chain watcher verifies and credits the user's internal account balance.

### Trading

- Bot fetches a quote from the private API.
- Bot submits a trade against the XYK AMM.
- Backend executes the swap, updates pool reserves, updates positions, and records the trade in the ledger.

### Withdrawal

- Bot submits a withdrawal request through the private API.
- Backend validates the request, sends Solana USDC from the platform treasury, and records the final state.

### Liquidity Seeding

- Anyone fetches the current market's public liquidity deposit address from the public API or the frontend.
- Anyone sends Solana USDC to that market address.
- Once credited, the deposit increases the market's `YES` and `NO` reserves equally.
- If this is the first credited liquidity deposit for that market, the market moves from `awaiting_liquidity` to `open` and the 90 day clock begins.

## Backend

### Core Modules

#### Auth

- challenge generation
- wallet-signature verification
- API key issuance
- API key management

#### Treasury

- user account deposit addresses
- market liquidity deposit addresses
- chain watchers
- deposit reconciliation
- withdrawal broadcast
- platform revenue sweeps

#### Ledger

- user cash balances
- positions
- trades
- payouts
- liquidity deposit records
- revenue transfer records
- immutable ledger history

#### Market Catalog

- token ingestion
- token pages
- current market per token
- past market history
- next-market creation on resolution

#### AMM Engine

- XYK reserve state
- quote generation
- swap execution
- liquidity seeding
- price derivation

#### Resolution

- hourly snapshot ingestion
- hourly reference price calculation
- ATH tracking
- threshold price calculation
- nuke evaluation
- market resolution

#### Admin

- halt and resume markets
- void markets
- inspect settlement data
- inspect treasury flows
- inspect market liquidity deposits

### Minimal Data Model

The backend needs, at minimum:

- tokens
- markets
- market snapshots
- market pools
- users
- API keys
- user account deposits
- user withdrawals
- market liquidity deposits
- positions
- trades
- ledger entries
- admin actions

Balances should be derived from the ledger, not from mutable cached totals alone.

## Market Lifecycle

States:

- `awaiting_liquidity`
- `open`
- `halted`
- `resolved_yes`
- `resolved_no`
- `void`

Rules:

- `awaiting_liquidity` means the market exists and has a public liquidity deposit address, but it is not tradable yet.
- `open` means the market is funded and tradable through the API.
- `halted` pauses trading.
- `resolved_yes` and `resolved_no` trigger payouts and revenue sweep.
- `void` is reserved for materially broken market setup or settlement data.
- On `resolved_yes` or `resolved_no`, create the next market for that token immediately in `awaiting_liquidity`.

Valid void reasons:

- invalid token-to-pair mapping
- materially incomplete settlement data
- invalid market creation record

## Admin And Operations

The MVP still needs a small admin surface.

### Treasury

- view user deposit flow
- view withdrawal flow
- view market liquidity deposits
- view revenue sweeps

### Market Operations

- search by mint or symbol
- inspect the current market and past markets for a token
- inspect tracked pairs
- preview settlement state
- halt, resume, or void a market

### Audit

- log every admin action with actor, timestamp, target, and reason

## Out Of Scope

The MVP does not need:

- order books
- any trading UI
- wallet connection in the frontend
- frontend portfolio pages
- frontend account pages
- frontend deposit or withdrawal pages
- liquidity withdrawals
- LP ownership shares
- notifications
- margin or leverage
- fiat rails
- non-USDC settlement

## Delivery Order

### First

- token ingestion
- rolling market catalog
- read-only frontend
- public API for token and market data

### Next

- API key auth
- user account deposits
- withdrawals
- account and portfolio API

### Then

- XYK AMM engine
- trading API
- market liquidity deposit flow
- hourly market snapshots
- ATH tracking and resolution
- next-market creation
- revenue sweep on resolution
