"""Build the executable notebook for Issue #44."""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf


HERE = Path(__file__).resolve().parent


def build() -> Path:
    notebook = nbf.v4.new_notebook()
    notebook["metadata"]["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    notebook["metadata"]["language_info"] = {"name": "python", "version": "3"}
    notebook["cells"] = [
        nbf.v4.new_markdown_cell(
            "# Issue #44 — levels_reversal vs ATR reversal\n"
            "Сравнение двух стратегий на общем портфеле 50,000 RUB. "
            "Ноутбук загружает зафиксированные JSON-прогоны, проверяет их контракт, "
            "строит метрики и воспроизводит все артефакты отчёта."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "import sys\n"
            "from IPython.display import display, Markdown, Image\n\n"
            "ANALYSIS_DIR = Path.cwd()\n"
            "if not (ANALYSIS_DIR / 'analysis.py').exists():\n"
            "    ANALYSIS_DIR = Path('reports/Vulpec/44_strategy-analysis').resolve()\n"
            "sys.path.insert(0, str(ANALYSIS_DIR))\n"
            "from analysis import run_analysis, DISPLAY_NAMES"
        ),
        nbf.v4.new_markdown_cell(
            "## Расчёт\n"
            "`run_analysis()` валидирует стратегию, стартовый капитал и структуру "
            "обоих входов. Daily equity строится по моментам закрытия сделок, поэтому "
            "это realized equity без mark-to-market."
        ),
        nbf.v4.new_code_cell("analysis = run_analysis()\nanalysis['metrics']"),
        nbf.v4.new_markdown_cell("## Equity-кривые"),
        nbf.v4.new_code_cell(
            "display(Image(filename=str(ANALYSIS_DIR / 'plots/equity_curves.png')))"
        ),
        nbf.v4.new_markdown_cell("## Распределение PnL сделок"),
        nbf.v4.new_code_cell(
            "display(Image(filename=str(ANALYSIS_DIR / "
            "'plots/trade_pnl_distribution.png')))"
        ),
        nbf.v4.new_markdown_cell("## Результаты по тикерам"),
        nbf.v4.new_code_cell(
            "ticker_pivot = analysis['tickers'].pivot(\n"
            "    index='ticker', columns='strategy', values=['n_trades', 'pnl_rub']\n"
            ")\n"
            "display(ticker_pivot)\n"
            "display(Image(filename=str(ANALYSIS_DIR / 'plots/ticker_pnl_heatmap.png')))"
        ),
        nbf.v4.new_markdown_cell("## Результаты по месяцам"),
        nbf.v4.new_code_cell(
            "monthly_pnl = analysis['monthly'].pivot(\n"
            "    index='month', columns='strategy', values='pnl_rub'\n"
            ").fillna(0)\n"
            "monthly_pnl"
        ),
        nbf.v4.new_markdown_cell("## GAME OVER"),
        nbf.v4.new_code_cell(
            "{strategy: {\n"
            "    'game_over': result.get('game_over'),\n"
            "    'game_over_ts': result.get('game_over_ts'),\n"
            "} for strategy, result in analysis['results'].items()}"
        ),
        nbf.v4.new_markdown_cell("## Итоговый отчёт"),
        nbf.v4.new_code_cell(
            "display(Markdown((ANALYSIS_DIR / 'report.md').read_text(encoding='utf-8')))"
        ),
    ]
    output = HERE / "analysis.ipynb"
    nbf.write(notebook, output)
    return output


if __name__ == "__main__":
    print(build())
