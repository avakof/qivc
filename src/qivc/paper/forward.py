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
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from qivc.schemas import MarketRegime

if TYPE_CHECKING:
    from qivc.backtest.portfolio_constructor import GridConfig, Position
    from qivc.config import Settings

_log = logging.getLogger(__name__)
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
    # 5 factor sub-scores at entry (insider/quality/valuation/momentum/technical),
    # for the dashboard micro-bars. Empty for older records / when unavailable.
    components: dict[str, float] = field(default_factory=dict)
    # Raw factor INPUTS at entry (fscore, gpa, insider_raw, valuation_raw,
    # momentum_raw, technical_raw) for the ticker drill-down. Empty -> "input detail
    # not recorded for this entry". Insider cluster aggregates are derived live from
    # form4_historical, not stored here.
    inputs: dict[str, float | int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperRecord:
    as_of: str  # ISO date of this paper snapshot
    config: str
    regime: str
    equity_pct: float  # invested fraction (rest is cash/T-bills)
    n_scored: int  # size of the insider-active scored universe
    positions: list[PaperPosition]
    # "fred_live" when the regime came from FRED; "fallback_neutral" when FRED was
    # unreachable and a neutral (risk-on) regime was substituted (sizing only).
    regime_source: str = "fred_live"
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
            regime_source=rec.get("regime_source", "fred_live"),
            disclaimer=rec.get("disclaimer", PaperRecord.disclaimer),
        )
    return last


def build_record(
    as_of: _dt.date,
    config: str,
    regime: MarketRegime,
    positions: list[Position],
    n_scored: int,
    components_by_ticker: dict[str, dict[str, float]] | None = None,
    regime_source: str = "fred_live",
    inputs_by_ticker: dict[str, dict[str, float | int | None]] | None = None,
) -> PaperRecord:
    """Build a PaperRecord from constructed positions.

    *components_by_ticker* maps ticker -> the 5 factor sub-scores at entry (for the
    dashboard micro-bars); *inputs_by_ticker* maps ticker -> the raw factor inputs
    (for the drill-down); omitted -> empty. *regime_source* records whether the
    regime came from FRED ("fred_live") or the neutral fallback.
    """
    comps = components_by_ticker or {}
    inps = inputs_by_ticker or {}
    pos = [
        PaperPosition(
            ticker=p.ticker,
            sector=p.sector,
            weight=round(p.weight, 4),
            composite=round(p.composite, 3),
            entry_date=p.entry_date.isoformat(),
            components={k: round(v, 3) for k, v in comps.get(p.ticker, {}).items()},
            inputs=inps.get(p.ticker, {}),
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
        regime_source=regime_source,
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
) -> tuple[
    list[Position], int, MarketRegime, dict[str, dict[str, float]], str,
    dict[str, dict[str, float | int | None]],
]:
    """
    Live single-date v3.0 build for *as_of*. Fetches regime, prices and EDGAR
    fundamentals for the insider-active universe, scores, and constructs the
    top-N book vs *prev_positions*. Returns (positions, n_scored, regime,
    components_by_ticker, regime_source, inputs_by_ticker).

    The daily record is the priority: the regime fetch degrades to a neutral
    fallback if FRED is unreachable, and the (non-essential) price/fundamentals
    fetches are guarded so a network hang there cannot kill the run.
    """
    import httpx

    from qivc.backtest import providers
    from qivc.backtest.composite_scorer import score_universe
    from qivc.backtest.pit_regime import regime_as_of_resilient
    from qivc.backtest.portfolio_constructor import construct_portfolio
    from qivc.backtest.universe import iwm_sector_map, iwm_tickers
    from qivc.backtest.v3_factors import assemble_factors, opportunistic_insider_raw
    from qivc.data.edgar_client import EdgarClient

    cfg = parse_config(config)
    sector_map = iwm_sector_map()
    universe = {t for t in iwm_tickers() if sector_map.get(t) not in _EXCLUDE_SECTORS}
    cache = providers.BacktestCache(Path(cache_dir) / as_of.isoformat())

    with httpx.Client(timeout=20.0) as hc:
        regime, regime_source = regime_as_of_resilient(as_of, client=hc)

    insider = opportunistic_insider_raw(settings.db_path, universe, as_of)  # local DB, no net
    active = sorted(insider)
    if active:
        # Prices (yfinance) are non-essential for the v3.0 baseline score; guard so a
        # transient fetch failure cannot break the daily record.
        try:
            providers.build_price_cache(
                cache, active,
                as_of - _dt.timedelta(days=_PRICE_WARMUP_DAYS),
                as_of + _dt.timedelta(days=3),
            )
        except Exception as exc:  # non-essential for the baseline score; must not crash
            _log.warning("paper: price fetch failed (non-essential): %s", exc)
        # Fundamentals (EDGAR) feed the quality factor; per-ticker failures already
        # degrade to neutral, but guard the bulk call against a total network hang.
        try:
            client = EdgarClient(
                settings.edgar_user_agent,
                rate_limit_rps=getattr(settings, "edgar_rate_limit_rps", 8),
            )
            providers.build_fundamentals_cache(cache, client, [(t, as_of) for t in active])
        except Exception as exc:  # quality degrades to neutral per missing-data rule
            _log.warning("paper: fundamentals fetch failed (quality -> neutral): %s", exc)
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
    components = {s.ticker: dict(s.components) for s in scored}
    inputs: dict[str, dict[str, float | int | None]] = {
        f.ticker: {
            "fscore": f.fscore, "gpa": f.gpa, "insider_raw": f.insider_raw,
            "valuation_raw": f.valuation_raw, "momentum_raw": f.momentum_raw,
            "technical_raw": f.technical_raw,
        }
        for f in factors
    }
    return positions, len(scored), regime, components, regime_source, inputs
