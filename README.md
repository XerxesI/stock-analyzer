# stock-analyzer

Python stock analysis toolkit with CLI scans, portfolio construction, and backtesting.

## Run (from repo root)

Always run module commands from the repository root so Python can resolve the package path correctly.

```powershell
python -m stock_analyzer.cli.main NBIS --period 1y
python -m stock_analyzer.cli.scan_all --confidence 0.5 --top 20 --portfolio
```

## Tests

```powershell
python -m pytest tests/test_backtest.py tests/test_fundamentals.py tests/test_portfolio.py -v
```

