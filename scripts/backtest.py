"""Offline walk-forward backtest (for long runs outside serverless limits).

Examples:
    python scripts/backtest.py --leagues E0 SP1 D1 I1 F1 --seasons 6 --folds 16
    python scripts/backtest.py --csv meus_jogos.csv --odds odds.csv --out relatorio.json

With DATABASE_URL set, the selected model version and its report are stored in
the model_versions table (``--register``).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cqe import db  # noqa: E402
from cqe.backtest import MODELS, odds_backtest, public, walk_forward  # noqa: E402
from cqe.providers import FOOTBALL_DATA  # noqa: E402
from engine import parse_matches, parse_odds  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="CSV de resultados (em vez do football-data.co.uk)")
    ap.add_argument("--leagues", nargs="*", default=["E0", "SP1", "D1", "I1", "F1"])
    ap.add_argument("--seasons", type=int, default=4)
    ap.add_argument("--folds", type=int, default=12)
    ap.add_argument("--line", type=float, default=9.5)
    ap.add_argument("--odds", help="CSV de odds históricas datadas para ROI")
    ap.add_argument("--min-ev", type=float, default=.03)
    ap.add_argument("--out", help="grava o relatório JSON neste arquivo")
    ap.add_argument("--register", action="store_true", help="grava a versão do modelo no banco (DATABASE_URL)")
    a = ap.parse_args()
    t = time.time()
    if a.csv:
        df = parse_matches(Path(a.csv).read_text(encoding="utf-8"))
    else:
        df, warns = FOOTBALL_DATA.history(a.leagues, a.seasons)
        for w in warns:
            print("aviso:", w, file=sys.stderr)
    print(f"{len(df)} partidas ({df.date.min().date()} a {df.date.max().date()}) em {time.time() - t:.1f}s")
    wf = walk_forward(df, n_folds=a.folds, main_line=a.line)
    report = public(wf)
    print(f"selecionado na validação: {wf['selected']} | validação {wf['n_validation']} | teste {wf['n_test']}")
    L = str(a.line)
    for m in wf["models"]:
        s = m["skill"][L]
        print(f"  {m['code']:15s} NLL teste {m['test']['nll']:.4f}  Brier {m['test']['lines'][L]['brier']:.4f}  "
              f"Δlogloss {s['delta_logloss']:+.4f}  {s['verdict']}")
    if a.odds:
        roi = odds_backtest(wf, parse_odds(Path(a.odds).read_bytes()), a.min_ev)
        report["roi"] = roi
        print(f"ROI: n={roi['n']} roi={roi['roi']} drawdown={roi['max_drawdown']:.2f} clv={roi['clv_mean']}")
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print("relatório:", a.out)
    if a.register:
        if not db.enabled():
            print("DATABASE_URL não configurada; nada gravado.", file=sys.stderr)
            return 1
        db.register_model(wf["selected_version"], wf["selected"], MODELS[wf["selected"]],
                          {k: report[k] for k in ("models", "folds", "n_test", "date_first", "date_last")})
        print("versão registrada:", wf["selected_version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
