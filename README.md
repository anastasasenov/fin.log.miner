## Portfolio Process Mining Pipeline![AI Assisted](https://shields.io)

An experimental modular Python framework that extracts historical financial asset data, constructs a synthetic blended index portfolio, discretizes continuous daily returns into symbolic behavioral event categories, and leverages Process Mining (Petri Nets) to discover and analyze behavioral transition workflows.

### Architecture


    { Portfolio String ( AAPL:0.5 MSFT:0.4 ...) } ->
    
    - Parse Tickers -> { Allocation Strategy } ->

    - API Extraction -> { yfinance Data Series } ->

    - Map Activity -> { Petry net ( PNML ) } -> 
    
    - Analysis -> { Report (JSON) }

### Requirements & Installation

$ pip install yfinance pandas pm4py


### Command-Line Interface Parameters

The pipeline can be executed as a comprehensive end-to-end workflow engine or split dynamically into isolated stages using operational runtime flags.

    -p PORTFOLIO, --portfolio PORTFOLIO Portfolio string, e.g. "AAPL:0.5, MSFT:0.5"
    -h HORIZON, --horizon HORIZON   Historical horizon in years (default: 5)
    -t NOISE_THRESHOLD, --noise-threshold NOISE_THRESHOLD   Inductive Miner noise threshold in [0, 1] (default: 0.15)
    -d DEPENDENCY_THRESHOLD, --dependency_threshold DEPENDENCY_THRESHOLD    Heuristics Miner dependency threshold in [0, 1] (default: 0.4)
    -f MAPPING_CONFIG, --mapping-config MAPPING_CONFIG  Path to custom activity mapping JSON
    -c CSV_OUT, --csv-out CSV_OUT   Event-log CSV path (default: /tmp/portfolio.csv)
    -n PNML_OUT, --pnml-out PNML_OUT    PNML path (default: /tmp/portfolio.pnml)
    -j JSON_OUT, --json-out JSON_OUT    JSON report path (default: /tmp/portfolio.json)
    -m {inductive,heuristic,split}, --method {inductive,heuristic,split}    Process discovery miner method.
    --phase-csv           Run Phase 1 only: download data and write event CSV
    --phase-pnml          Run Phase 2 only: read CSV and write PNML
    --phase-json          Run Phase 3 only: read CSV/PNML and write JSON
    
### Data Transformation & Mapping Configurations


* Default Mapping Profile

    If no configuration file path is passed to --mapping-config, the discretization engine maps individual daily return yields (R) across a standard five-tier tier architecture:

        HIGH_GAIN: R > 1.5%
        MODERATE_GAIN: 0.2% < R <= 1.5%
        STABLE: -0.2% <= R <= 0.2%
        MODERATE_LOSS: -1.5% <= R < -0.2%
        HIGH_LOSS: R < -1.5%

* Custom Rule Injection Format (custom_rules.json)

    You can supply highly granular tracking definitions via an external JSON template. The runtime structure supports evaluation conditions such as gt (>), gte (>=), lt (<), and lte (<=):

    {
    "mapping_name": "High Volatility Asset Boundaries",
    "rules": [
            {
            "activity": "EXTREME_SHOCK_UP",
            "conditions": { "gt": 0.04 }
            },
            {
            "activity": "BULL_MARKET",
            "conditions": { "gt": 0.005, "lte": 0.04 }
            },
            {
            "activity": "CONSOLIDATION",
            "conditions": { "gte": -0.005, "lte": 0.005 }
            },
            {
            "activity": "BEAR_MARKET",
            "conditions": { "lt": -0.005, "gte": -0.04 }
            },
            {
            "activity": "EXTREME_SHOCK_DOWN",
            "conditions": { "lt": -0.04 }
            }
        ]
    }

### Usage


End-to-End Pipeline Execution

    $ python3 fin.log.miner.py --portfolio "AAPL:0.3, MSFT:0.3, GOOG:0.4" --horizon 3


Segmented Execution Modes

    Phase 1: Ingest data and generate custom log maps

    $ python3 fin.log.miner.py -p "TSLA:0.5, NVDA:0.5" --phase-csv --mapping-config custom_rules.json --csv-out tech_returns.csv

    Phase 2: Generate a noise-filtered Petri Net from a CSV log

    $ python3 fin.log.miner.py --phase-pnml --csv-out tech_returns.csv --noise-threshold 0.15 --pnml-out filtered_tech.pnml

    Phase 3: Generate diagnostic conformance summaries

    $python3 fin.log.miner.py --phase-json --csv-out tech_returns.csv --pnml-out filtered_tech.pnml --json-out final_report.json

### Inspired by

PM4Py is a python library that supports state-of-the-art process mining algorithms in Python. ( https://github.com/process-intelligence-solutions/pm4py )

### License

This project is open-source and available under the MIT License. Feel free to modify and adapt it.

