# Paper-mode risk control module

The risk-control module lives in `risk_control.py`; tests are in `tests/test_risk_control.py`.

## Fixed defaults

- Paper capital: **1000 USDT**
- Maximum position notional: **100 USDT**
- Daily realized net-loss stop: **-5 USDT**, fees included
- Daily P&L is grouped by UTC calendar day
- SQLite ledger persists across restarts; execution IDs must be unique
- Errors and invalid inputs raise exceptions; callers must treat every exception as a hard block (fail closed)

## Run tests locally or in Codespaces

Python 3.10+ is recommended. From the repository root, run:

```bash
python -m unittest discover -s tests -v
```

No GitHub Actions workflow is included. No exchange connection, API credentials, live orders, deployment, or automated trading is included. This module only provides a risk gate and ledger; it does not execute trades.

## Open in Codespaces

Open the repository in GitHub, select **Code → Codespaces → Create codespace on main**, then run the test command above in the terminal. Codespaces usage is subject to the account's available quota.
