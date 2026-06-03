"""
Forward paper-trading ledger + live v3.0 portfolio assembly for one as-of date.

`assemble_paper_portfolio` mirrors a single rebalance of `run_backtest_grid`
(insider-active universe -> live prices/fundamentals -> composite score ->
top-N construction), but for a forward, unseen date. The result is appended to a
JSONL ledger as an *intended book* snapshot. NO orders are placed; this is a
research data-collection tool with no claim of edge (see STRATEGY_NOTES.md).

The ledger is a sequence of monthly snapshots: each run reads the prior record for
the same config and threads its positions in, so the holding-period persistence
rule applies across paper runs exactly as in the backtest.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from qivc.schemas import MarketRegime

if TYPE_CHECKING:
    from qivc.backtest.portfolio_constructor import GridConfig, Position
    from qivc.config import Settings

_EXCLUDE_SECTORS = {"Financials", "Real Estate"}
_PRICE_WARMUP_DAYS = 95
DEFAULT_LEDGER = "data/paper/ledger.jsonl"
DEFAULT_CONFIG = "N10_thr90_hold30"

_CONFIG_RE = re.compile(r"^N(\d+)_thr(\d+)_hold(\d+)$")


@dataclass(frozen=True)
class PaperPosition:
    ticker: str
    sector: str
    weight: float
    composite: float
    entry_date: str  # ISO date the name was first entered


@dataclass(frozen=True)
class PaperRecord:
    as_of: str  # ISO date of this paper snapshot
    config: str
    regime: str
    equity_pct: float  # invested fraction (rest is cash/T-bills)
    n_scored: int  # size of the insider-active scored universe
    positions: list[PaperPosition]
    disclaimer: str = (
        "RESEARCH ONLY — paper/intended book, NO orders placed, NO demonstrated "
        "edge. v3.0 is survivor-biased in-sample; this forward record is for "
        "out-of-sample validation only. See STRATEGY_NOTES.md (DO NOT DEPLOY)."
    )


def parse_config(label: str) -> GridConfig:
    """Parse 'N{n}_thr{thr}_hold{hold}' into a GridConfig (lazy import)."""
    m = _CONFIG_RE.match(label)
    if m is None:
        raise ValueError(
            f"bad config '{label}'; expected e.g. 'N10_thr90_hold30'"
        )
    from qivc.backtest.portfolio_constructor import GridConfig

    return GridConfig(int(m.group(1)), float(m.group(2)), int(m.group(3)))


def read_last_record(ledger_path: str | Path, config: str) -> PaperRecord | None:
    """Return the most recent ledger record for *config*, or None."""
    p = Path(ledger_path)
    if not p.exists():
        return None
    last: PaperRecord | None = None
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("config") != config:
            continue
        last = PaperRecord(
            as_of=rec["as_of"],
            config=rec["config"],
            regime=rec["regime"],
            equity_pct=rec["equity_pct"],
            n_scored=rec["n_scored"],
            positions=[PaperPosition(**pos) for pos in rec["positions"]],
            disclaimer=rec.get("disclaimer", PaperRecord.disclaimer),
        )
    return last


def build_record(
    as_of: _dt.date,
    config: str,
    regime: MarketRegime,
    positions: list[Position],
    n_scored: int,
) -> PaperRecord:
    """Build a PaperRecord from constructed positions."""
    pos = [
        PaperPosition(
            ticker=p.ticker,
            sector=p.sector,
            weight=round(p.weight, 4),
            composite=round(p.composite, 3),
            entry_date=p.entry_date.isoformat(),
        )
        for p in sorted(positions, key=lambda p: -p.weight)
    ]
    return PaperRecord(
        as_of=as_of.isoformat(),
        config=config,
        regime=regime.regime,
        equity_pct=round(sum(p.weight for p in positions) * 100, 1),
        n_scored=n_scored,
        positions=pos,
    )


def append_record(ledger_path: str | Path, record: PaperRecord) -> None:
    """Append one record as a JSON line (creates parent dir + file)."""
    p = Path(ledger_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    with p.open("a") as fh:
        fh.write(json.dumps(payload) + "\n")


def assemble_paper_portfolio(
    settings: Settings,
    as_of: _dt.date,
    config: str,
    prev_positions: list[Position],
    cache_dir: str = "data/paper/_cache",
) -> tuple[list[Position], int, MarketRegime]:
    """
    Live single-date v3.0 build for *as_of*. Fetches regime, prices and EDGAR
    fundamentals for the insider-active universe, scores, and constructs the
    top-N book vs *prev_positions*. Returns (positions, n_scored, regime).
    """
    import httpx

    from qivc.backtest import providers
    from qivc.backtest.composite_scorer import score_universe
    from qivc.backtest.pit_regime import regime_as_of
    from qivc.backtest.portfolio_constructor import construct_portfolio
    from qivc.backtest.universe import iwm_sector_map, iwm_tickers
    from qivc.backtest.v3_factors import assemble_factors, opportunistic_insider_raw
    from qivc.data.edgar_client import EdgarClient

    cfg = parse_config(config)
    sector_map = iwm_sector_map()
    universe = {t for t in iwm_tickers() if sector_map.get(t) not in _EXCLUDE_SECTORS}
    cache = providers.BacktestCache(Path(cache_dir) / as_of.isoformat())

    with httpx.Client(timeout=30.0) as hc:
        regime = regime_as_of(as_of, client=hc)

    insider = opportunistic_insider_raw(settings.db_path, universe, as_of)
    active = sorted(insider)
    if active:
        providers.build_price_cache(
            cache,
            active,
            as_of - _dt.timedelta(days=_PRICE_WARMUP_DAYS),
            as_of + _dt.timedelta(days=3),
        )
        client = EdgarClient(
            settings.edgar_user_agent,
            rate_limit_rps=getattr(settings, "edgar_rate_limit_rps", 8),
        )
        providers.build_fundamentals_cache(cache, client, [(t, as_of) for t in active])
    fund_provider = providers.make_fundamentals_provider(cache)

    factors = assemble_factors(
        as_of,
        db_path=settings.db_path,
        universe=universe,
        sector_map=sector_map,
        fundamentals_provider=fund_provider,
    )
    scored = score_universe(factors)
    positions = construct_portfolio(scored, prev_positions, as_of, cfg, regime)
    return positions, len(scored), regime
