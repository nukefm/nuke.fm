# do now
- make the pm predict what the price will be of the token by the date in the future, by replacing yes and no outcomes (binary) with long and short outcomes (scalar) where long shares are redeemable according to the price at the date of the market's resolution. the payout function is long = $1 * (memecoinPriceAtResolution/maxPrice) and short = $1 - long
- maxPrice is defined as priceAtMarketCreation * 10x. if the underlying price goes above maxPrice, for 24hr, that should trigger a new market to be created.
    - pls auto-move 80% of liq from old into new mkt. make this 80% configurable.
- also update the predicted nuke % calculation so that it just shows the % nuke implied by the predicted mktcap 
- also i actually want long calc to use a log scale (and therefore also short by implication given it is 1- long). because i think a log scale is more aligned with the volatility of memecoins even though it has a bit of a downwards bias?

# ignore for now
- deferred: calibrate period and drawdown % from historical data
- create trading bot
- improve ui a lot. it kinda sucks rn
- improve wording. currently it is somewhat slop