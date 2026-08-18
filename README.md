## Kalshi Greeks Screen

This repo is a minimalistic terminal screen for Kalshi contract greeks. 

Currently, only BTC is available. ETH might be added soon, as well commodities and indexes.

### Usage

```
python greeks_screen.py (volume threshold) (time threshold)
```

Currently, this only track BTC 15m/hourly/daily up-down contracts. The screen will only show contracts volume above the threshold and remaining time below the threshold

If the last 2 arguments are not given, we default to 10000 (volume) and 3601 (remaining seconds).


### Math

Assumes GBM for the underlying S, so for a YES contract C at strike K maturing in time T, the price is

$$C(K,T) = P(S_T  > K) = \Phi(d_2 )$$

where $\Phi$ is the normal CDF and $d_2$ the usual quantity from BS.

Please check the code for the other greeks.
