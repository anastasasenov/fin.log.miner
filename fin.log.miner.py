#!/usr/bin/env python3

# Process Mining Framework for Synthetic Portfolio Indexes
#
# Dependencies:
#  pip install yfinance pandas pm4py

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import pandas as pd

# constants
EPSILON = 1e-4
PORTFOLIO_RE = re.compile(r"([A-Za-z0-9\-.]+)[?:\s,;]+([0-9.]+)")
DEFAULT_CSV = "/tmp/portfolio.csv"
DEFAULT_PNML = "/tmp/portfolio.pnml"
DEFAULT_JSON = "/tmp/portfolio.json"

DEFAULT_RULES: List[Dict[str, Any]] = [
  {"activity": "HIGH_GAIN", "conditions": {"gt": 0.015}},
  {"activity": "MODERATE_GAIN", "conditions": {"gt": 0.002, "lte": 0.015}},
  {"activity": "STABLE", "conditions": {"gte": -0.002, "lte": 0.002}},
  {"activity": "MODERATE_LOSS", "conditions": {"lt": -0.002, "gte": -0.015}},
  {"activity": "HIGH_LOSS", "conditions": {"lt": -0.015}},
]


def parse_portfolio(
  raw: str
  ) -> Dict[str, float]:
  """parse portfolio ticker/weight ... pairs"""
  if not raw or not raw.strip():
    raise ValueError("Portfolio string is empty.")

  matches = list(PORTFOLIO_RE.finditer(raw))
  if not matches:
    raise ValueError(
      "Could not parse any ticker/weight pairs. Example: AAPL:0.5, MSFT:0.5"
    )

  # reg-ex for parsing
  positions: Dict[str, float] = {}
  for match in matches:
    ticker = match.group(1).strip().upper()
    weight_text = match.group(2)
    try:
      weight = float(weight_text)
    except ValueError as exc:
      raise ValueError(f"Invalid weight for {ticker}: {weight_text!r}") from exc
    if not ticker:
      raise ValueError("Ticker must not be empty.")
    if not math.isfinite(weight) or weight < 0:
      raise ValueError(f"Weight for {ticker} must be finite and non-negative.")
    positions[ticker] = positions.get(ticker, 0.0) + weight

  if not positions:
    raise ValueError("No valid portfolio positions were found.")
  total = sum(positions.values())
  if abs(1.0 - total) > EPSILON:
    raise ValueError(
      f"Portfolio weights must sum to 1.0 ({EPSILON:g}); got {total:.10g}."
    )
  if total <= 0:
    raise ValueError("Portfolio weights must have a positive total.")

  # nomalization
  return {ticker: weight / total for ticker, weight in positions.items()}

def load_mapping(
  path: Optional[str]
  ) -> Tuple[List[Dict[str, Any]], str]:
  """load and validate mapping rules, otherwise defaults"""
  if not path:
    return DEFAULT_RULES, "default"
  config_path = Path(path)
  try:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
  except OSError as exc:
    raise RuntimeError(f"Cannot read mapping configuration {path!r}: {exc}") from exc
  except json.JSONDecodeError as exc:
    raise RuntimeError(f"Invalid JSON in mapping configuration: {exc}") from exc

  rules = payload.get("rules") if isinstance(payload, dict) else None
  if not isinstance(rules, list) or not rules:
    raise ValueError("Mapping JSON must contain a non-empty 'rules' array.")

  valid_ops = {"gt", "gte", "lt", "lte"}
  cleaned: List[Dict[str, Any]] = []
  names = set()
  for i, rule in enumerate(rules):
    if not isinstance(rule, dict):
      raise ValueError(f"Rule #{i + 1} must be a JSON object.")
    activity = rule.get("activity")
    conditions = rule.get("conditions")
    if not isinstance(activity, str) or not activity.strip():
      raise ValueError(f"Rule #{i + 1} needs a non-empty activity string.")
    if activity in names:
      raise ValueError(f"Duplicate activity name {activity!r} in mapping.")
    names.add(activity)
    if not isinstance(conditions, dict) or not conditions:
      raise ValueError(f"Rule {activity!r} needs non-empty conditions.")
    if set(conditions) - valid_ops:
      raise ValueError(
        f"Rule {activity!r} uses unsupported operators: "
        f"{sorted(set(conditions) - valid_ops)}"
      )
    numeric_conditions: Dict[str, float] = {}
    for op, value in conditions.items():
      if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{activity}.{op} must be numeric.")
      if not math.isfinite(float(value)):
        raise ValueError(f"{activity}.{op} must be finite.")
      numeric_conditions[op] = float(value)
    if "gt" in numeric_conditions and "lte" in numeric_conditions:
      if numeric_conditions["gt"] >= numeric_conditions["lte"]:
        raise ValueError(f"Invalid interval in rule {activity!r}.")
    if "gte" in numeric_conditions and "lt" in numeric_conditions:
      if numeric_conditions["gte"] >= numeric_conditions["lt"]:
        raise ValueError(f"Invalid interval in rule {activity!r}.")
    cleaned.append({"activity": activity.strip(), "conditions": numeric_conditions})

  return cleaned, str(config_path)

def classify_return(
  value: float,
  rules: Sequence[Dict[str, Any]]
  ) -> str:
  """return the first the activity that match"""
  for rule in rules:
    conditions = rule["conditions"]
    matches = True
    for op, bound in conditions.items():
      if op == "gt" and not value > bound:
        matches = False
      elif op == "gte" and not value >= bound:
        matches = False
      elif op == "lt" and not value < bound:
        matches = False
      elif op == "lte" and not value <= bound:
        matches = False
    if matches:
      return rule["activity"]
  raise ValueError(
    f"No activity mapping rule matched return {value:.10g}. "
    "Check that your custom rules cover the full real number line."
  )


def download_prices(
  weights: Dict[str, float],
  horizon_years: int
  ) -> pd.DataFrame:
  """download prices / yfinance"""
  if horizon_years < 1 or horizon_years > 100:
    raise ValueError("--horizon must be an integer from 1 to 100.")
  try:
    import yfinance as yf
  except ImportError as exc:
    raise RuntimeError("Missing dependency 'yfinance'. Install with: pip install yfinance") from exc

  end = datetime.now()
  start = end - timedelta(days=round(horizon_years * 365.2425))
  tickers = list(weights)
  try:
    downloaded = yf.download(
      tickers=tickers,
      start=start.date().isoformat(),
      end=(end + timedelta(days=1)).date().isoformat(),
      auto_adjust=True,
      progress=False,
      group_by="column",
      threads=True,
    )
  except Exception as exc:
    raise RuntimeError(f"Market-data download failed: {exc}") from exc

  if downloaded is None or downloaded.empty:
    raise RuntimeError("yfinance returned no price data.")

  # process multiindex response
  if isinstance(downloaded.columns, pd.MultiIndex):
    level0 = downloaded.columns.get_level_values(0)
    field = "Close" if "Close" in level0 else (
      "Adj Close" if "Adj Close" in level0 else None
    )
    if field is None:
      raise RuntimeError("Downloaded data has neither Close nor Adj Close prices.")
    close = downloaded[field].copy()
  else:
    field = "Close" if "Close" in downloaded.columns else (
      "Adj Close" if "Adj Close" in downloaded.columns else None
    )
    if field is None:
      raise RuntimeError("Downloaded data has neither Close nor Adj Close prices.")
    ticker = tickers[0]
    close = downloaded[[field]].rename(columns={field: ticker})
  if isinstance(close, pd.Series):
    close = close.to_frame(name=tickers[0])
  close.columns = [str(c) for c in close.columns]
  missing = [ticker for ticker in tickers if ticker not in close.columns]
  if missing:
    raise RuntimeError(
      "No usable price series returned for ticker(s): " + ", ".join(missing)
    )
  close = close[tickers].apply(pd.to_numeric, errors="coerce")
  close = close.replace([float("inf"), float("-inf")], pd.NA)
  close = close.ffill().bfill()
  close = close.dropna(how="any")
  if len(close) < 3:
    raise RuntimeError("Insufficient aligned price history to calculate returns.")
  if (close <= 0).any().any():
    raise RuntimeError("Price history contains non-positive prices.")
  return close


def build_event_log(
  portfolio: Dict[str, float],
  horizon_years: int,
  rules: Sequence[Dict[str, Any]],
  mapping_source: str,
  ) -> pd.DataFrame:
  """build csv - cases"""
  prices = download_prices(portfolio, horizon_years)
  returns = prices.pct_change(fill_method=None).dropna(how="any")
  if returns.empty:
    raise RuntimeError("No returns remain after price alignment.")
  weights = pd.Series(portfolio, dtype=float)
  index_returns = returns.mul(weights, axis="columns").sum(axis=1)
  events = []
  for timestamp, value in index_returns.items():
    value = float(value)
    events.append({
      "case_id": pd.Timestamp(timestamp).strftime("%Y-%m"),
      "activity": classify_return(value, rules),
      "timestamp": pd.Timestamp(timestamp).isoformat(),
      "index_return": value,
      "source": mapping_source,
    })
  return pd.DataFrame(events, columns=[
    "case_id", "activity", "timestamp", "index_return", "source"
  ])

def read_event_log(
  csv_path: str
  ) -> pd.DataFrame:
  """read and validate csv"""
  path = Path(csv_path)
  if not path.exists():
    raise RuntimeError(f"Event log CSV does not exist: {csv_path}")
  try:
    df = pd.read_csv(path)
  except Exception as exc:
    raise RuntimeError(f"Could not read event log CSV {csv_path!r}: {exc}") from exc
  required = {"case_id", "activity", "timestamp"}
  missing = required - set(df.columns)
  if missing:
    raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
  if df.empty:
    raise ValueError("Event log CSV contains no events.")
  df["case_id"] = df["case_id"].astype(str)
  df["activity"] = df["activity"].astype(str)
  df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
  if df["timestamp"].isna().any():
    raise ValueError("Event log contains invalid timestamps.")
  if (df["activity"].str.strip() == "").any():
    raise ValueError("Event log contains empty activity labels.")
  df = df.sort_values(["case_id", "timestamp"], kind="stable").reset_index(drop=True)
  return df

def get_pm4py():
  try:
    import pm4py
    return pm4py
  except ImportError as exc:
    raise RuntimeError(
      "Missing dependency 'pm4py'. Install with: pip install pm4py"
    ) from exc


def discover_and_export_petri_net(
  df: pd.DataFrame,
  pnml_path: str,
  noise_threshold: float,
  dependency_threshold: float,
  method: str
  ) -> Tuple[Any, Any, Any]:
  """ discover Petri net """
  if not 0.0 <= noise_threshold <= 1.0:
    raise ValueError("--noise-threshold must be between 0.0 and 1.0.")
  if not 0.0 <= dependency_threshold <= 1.0:
    raise ValueError("--noise-dependency_threshold must be between 0.0 and 1.0.")
  pm4py = get_pm4py()
  try:
    # event-log -> cases
    event_df = df.copy()
    event_df["case:concept:name"] = event_df["case_id"].astype(str)
    event_df["concept:name"] = event_df["activity"].astype(str)
    event_df["time:timestamp"] = pd.to_datetime(event_df["timestamp"])
    try:
      if "split" == method:
        net, initial_marking, final_marking = pm4py.discover_bpmn_split_miner(
          event_df,
          case_id_key="case:concept:name",
          activity_key="concept:name",
          timestamp_key="time:timestamp"
        )
      elif "heuristic" == method:
        net, initial_marking, final_marking = pm4py.discover_heuristics_net(
          event_df,
          case_id_key="case:concept:name",
          activity_key="concept:name",
          timestamp_key="time:timestamp",
          dependency_threshold=dependency_threshold
        )
      else:
        net, initial_marking, final_marking = pm4py.discover_petri_net_inductive(
          event_df,
          case_id_key="case:concept:name",
          activity_key="concept:name",
          timestamp_key="time:timestamp",
          noise_threshold=noise_threshold,
        )
    except TypeError:
      # compatibility
      event_log = pm4py.convert_to_event_log(
        event_df,
        case_id_key="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
      )
      from pm4py.algo.discovery.inductive import algorithm as inductive_miner
      from pm4py.objects.conversion.process_tree import converter as pt_converter
      parameters = {"noise_threshold": noise_threshold}
      tree = inductive_miner.apply(event_log, parameters=parameters)
      net, initial_marking, final_marking = pt_converter.apply(
        tree, variant=pt_converter.Variants.TO_PETRI_NET
      )
    out = Path(pnml_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pm4py.write_pnml(net, initial_marking, final_marking, str(out))
    return net, initial_marking, final_marking
  except Exception as exc:
    raise RuntimeError(f"Petri-net discovery/export failed: {exc}") from exc

def load_petri_net(
  pnml_path: str
  ) -> Tuple[Any, Any, Any]:
  """load pnml-file"""
  path = Path(pnml_path)
  if not path.exists():
    raise RuntimeError(f"PNML model does not exist: {pnml_path}")
  pm4py = get_pm4py()
  try:
    return pm4py.read_pnml(str(path))
  except Exception as exc:
    raise RuntimeError(f"Could not read PNML file {pnml_path!r}: {exc}") from exc



def calculate_conformance(
  df: pd.DataFrame,
  net: Any,
  initial_marking: Any,
  final_marking: Any
  ) -> Dict[str, Optional[float]]:
  """calculate fitness"""
  pm4py = get_pm4py()
  event_df = df.copy()
  event_df["case:concept:name"] = event_df["case_id"].astype(str)
  event_df["concept:name"] = event_df["activity"].astype(str)
  event_df["time:timestamp"] = pd.to_datetime(event_df["timestamp"])
  result: Dict[str, Optional[float]] = {"fitness": None, "precision": None}
  try:
    log = pm4py.convert_to_event_log(
      event_df,
      case_id_key="case:concept:name",
      activity_key="concept:name",
      timestamp_key="time:timestamp",
    )
  except Exception:
    log = None

  if log is not None:
    try:
      from pm4py.algo.conformance.alignments.petri_net import algorithm as alignments
      aligned = alignments.apply_log(
        log, net, initial_marking, final_marking
      )
      if aligned:
        fitness_values = []
        for item in aligned:
          fit = item.get("fitness")
          if fit is not None and math.isfinite(float(fit)):
            fitness_values.append(float(fit))
        if fitness_values:
          result["fitness"] = sum(fitness_values) / len(fitness_values)
    except Exception:
      pass
    if result["fitness"] is None:
      try:
        from pm4py.algo.conformance.tokenreplay import algorithm as token_replay
        replayed = token_replay.apply(
          log, net, initial_marking, final_marking
        )
        values = [
          float(item["trace_fitness"]) for item in replayed
          if item.get("trace_fitness") is not None
        ]
        if values:
          result["fitness"] = sum(values) / len(values)
      except Exception:
        pass
    try:
      from pm4py.algo.evaluation.precision import algorithm as precision_eval
      precision_value = precision_eval.apply(
        log, net, initial_marking, final_marking
      )
      if precision_value is not None and math.isfinite(float(precision_value)):
        result["precision"] = float(precision_value)
    except Exception:
      pass
  return result

def safe_number(
  value: Optional[float]
  ) -> Optional[float]:
  if value is None or not math.isfinite(float(value)):
    return None
  return round(float(value), 6)

def build_report(
  df: pd.DataFrame,
  net: Any,
  metrics: Dict[str, Optional[float]],
  horizon_years: int,
  noise_threshold: float,
  mapping_source: str,
  ) -> Dict[str, Any]:
  """build json-report"""
  distribution = Counter(df["activity"].astype(str))
  ordered_distribution = [
    {"activity": activity, "count": int(count)}
    for activity, count in distribution.most_common()
  ]
  return {
    "execution_timestamp": datetime.now().isoformat(timespec="microseconds"),
    "pipeline_parameters": {
      "horizon_years": int(horizon_years),
      "noise_threshold": float(noise_threshold),
      "mapping_source": mapping_source,
    },
   "petri_net_stats": {
      "number_of_places": len(getattr(net, "places", [])),
      "number_of_transitions": len(getattr(net, "transitions", [])),
      "number_of_arcs": len(getattr(net, "arcs", [])),
    },
    "process_mining_metrics": {
      "fitness": safe_number(metrics.get("fitness")),
      "precision": safe_number(metrics.get("precision")),
    },
    "frequent_patterns": {
      "total_events": int(len(df)),
      "unique_cases": int(df["case_id"].nunique()),
      "distribution": ordered_distribution,
    },
  }

def write_json(
  payload: Dict[str, Any],
  path: str
  ) -> None:
  out = Path(path)
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="Mine a Petri net from synthetic portfolio",
    add_help=False,
  )
  parser.add_argument("-?", "--help", action="help", help="Show this help message and exit")
  parser.add_argument("-p", "--portfolio", help='Portfolio string, e.g. "AAPL:0.5, MSFT:0.5"')
  parser.add_argument("-h", "--horizon", type=int, default=5, help="Historical horizon in years (default: 5)")
  parser.add_argument("-t", "--noise-threshold", type=float, default=0.15, help="Inductive Miner noise threshold in [0, 1] (default: 0.15)")
  parser.add_argument("-d", "--dependency_threshold", type=float, default=0.4, help="Heuristics Miner dependency threshold in [0, 1] (default: 0.4)")
  parser.add_argument("-f", "--mapping-config", help="Path to custom activity mapping JSON")
  parser.add_argument("-c", "--csv-out", default=DEFAULT_CSV, help=f"Event-log CSV path (default: {DEFAULT_CSV})")
  parser.add_argument("-n", "--pnml-out", default=DEFAULT_PNML, help=f"PNML path (default: {DEFAULT_PNML})")
  parser.add_argument("-j", "--json-out", default=DEFAULT_JSON, help=f"JSON report path (default: {DEFAULT_JSON})")
  parser.add_argument("-m", "--method", choices=["inductive", "heuristic", "split"], default="inductive", help="Process discovery miner method." )
  parser.add_argument("--phase-csv", action="store_true", help="Run Phase 1 only: download data and write event CSV")
  parser.add_argument("--phase-pnml", action="store_true", help="Run Phase 2 only: read CSV and write PNML")
  parser.add_argument("--phase-json", action="store_true", help="Run Phase 3 only: read CSV/PNML and write JSON")
  return parser

def run(args: argparse.Namespace) -> None:
  selected_phases = [args.phase_csv, args.phase_pnml, args.phase_json]
  if sum(selected_phases) > 1:
    raise ValueError("Choose at most one of --phase-csv, --phase-pnml, or --phase-json.")
  if not 0.0 <= args.noise_threshold <= 1.0:
    raise ValueError("--noise-threshold must be between 0.0 and 1.0.")

  # Phase 1: parse portfolio, download prices, write csv-file
  if args.phase_csv or not any(selected_phases):
    if not args.portfolio:
      raise ValueError("--portfolio is required for the full pipeline and --phase-csv.")
    portfolio = parse_portfolio(args.portfolio)
    rules, mapping_source = load_mapping(args.mapping_config)
    events = build_event_log(portfolio, args.horizon, rules, mapping_source)
    out = Path(args.csv_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(out, index=False, date_format="%Y-%m-%dT%H:%M:%S")
    print(f"Phase 1 complete: wrote {len(events)} events to {out}")
    if args.phase_csv:
      return

 # Phase 2: export pnml-file
  if args.phase_pnml:
    df = read_event_log(args.csv_out)
    discover_and_export_petri_net(
      df,
      args.pnml_out,
      args.noise_threshold,
      args.dependency_threshold,
      args.method)
    print(f"Phase 2 complete: wrote Petri net to {args.pnml_out}")
    return

  # pipeline: Phase 1/2
  if not any(selected_phases):
    df = read_event_log(args.csv_out)
    net, im, fm = discover_and_export_petri_net(
      df,
      args.pnml_out,
      args.noise_threshold,
      args.dependency_threshold,
      args.method
    )
    print(f"Phase 2 complete: wrote Petri net to {args.pnml_out}")
    metrics = calculate_conformance(df, net, im, fm)
    report = build_report(
      df, net, metrics, args.horizon, args.noise_threshold, mapping_source
    )
    write_json(report, args.json_out)
    print(f"Phase 3 complete: wrote analysis report to {args.json_out}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return

  # Phase 3: load pnml-file and report json-file
  if args.phase_json:
    df = read_event_log(args.csv_out)
    net, im, fm = load_petri_net(args.pnml_out)
    metrics = calculate_conformance(df, net, im, fm)
    mapping_source = "mapping_source column" if "source" in df.columns else "unknown"
    if "source" in df.columns and not df["source"].dropna().empty:
      mapping_source = str(df["source"].dropna().iloc[0])
    report = build_report(
      df, net, metrics, args.horizon, args.noise_threshold, mapping_source
    )
    write_json(report, args.json_out)
    print(f"Phase 3 complete: wrote analysis report to {args.json_out}")
    print(json.dumps(report, indent=2, ensure_ascii=False))

def main(argv: Optional[Sequence[str]] = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  try:
    run(args)
    return 0
  except (ValueError, RuntimeError) as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    return 2
  except KeyboardInterrupt:
    print("Interrupted.", file=sys.stderr)
    return 130


if __name__ == "__main__":
  raise SystemExit(main())
