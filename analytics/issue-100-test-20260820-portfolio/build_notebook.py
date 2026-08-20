"""Build the executable notebook for Issue #100."""

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
            "# Issue #100 — портфельный бэктест test_20260820\n"
            "Один конфиг (`trading.strategies.id=102`) на общем портфеле 50,000 RUB. "
            "Ноутбук загружает зафиксированный JSON-прогон, проверяет контракт, "
            "строит метрики и воспроизводит артефакты отчёта. ATR не сравнивается."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "import sys\n"
            "from IPython.display import display, Markdown, Image\n\n"
            "ANALYSIS_DIR = Path.cwd()\n"
            "if not (ANALYSIS_DIR / 'analysis.py').exists():\n"
            "    ANALYSIS_DIR = Path("
            "'analytics/issue-100-test-20260820-portfolio').resolve()\n"
            "sys.path.insert(0, str(ANALYSIS_DIR))\n"
            "from analysis import run_analysis, DISPLAY_NAME"
        ),
        nbf.v4.new_markdown_cell(
            "## Расчёт\n"
            "`run_analysis()` валидирует id=102, стартовый капитал, слоты и покрытие "
            "2026-08-20. Daily equity строится по моментам закрытия сделок "
            "(realized equity без mark-to-market). Бар ALRS paper #711 во входах "
            "является блокером."
        ),
        nbf.v4.new_code_cell("analysis = run_analysis()\nanalysis['metrics']"),
        nbf.v4.new_markdown_cell("## Конфиг и вселенная"),
        nbf.v4.new_code_cell(
            "result = analysis['result']\n"
            "{\n"
            "    'strategy_id': result.get('strategy_id'),\n"
            "    'strategy_config_name': result.get('strategy_config_name'),\n"
            "    'plugin': result.get('strategy'),\n"
            "    'in_paper_test': result.get('in_paper_test'),\n"
            "    'locked': result.get('locked'),\n"
            "    'date_from': result.get('date_from'),\n"
            "    'date_to': result.get('date_to'),\n"
            "    'period_last_day': result.get('period_last_day'),\n"
            "    'tickers_volume_order': result.get('tickers_volume_order'),\n"
            "    'sha256': analysis['hash'],\n"
            "}"
        ),
        nbf.v4.new_markdown_cell("## Equity-кривая"),
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
            "display(analysis['tickers'])\n"
            "display(Image(filename=str(ANALYSIS_DIR / 'plots/ticker_pnl_heatmap.png')))"
        ),
        nbf.v4.new_markdown_cell("## Результаты по месяцам"),
        nbf.v4.new_code_cell("analysis['monthly']"),
        nbf.v4.new_markdown_cell("## ALRS paper #711 и GAME OVER"),
        nbf.v4.new_code_cell(
            "{\n"
            "    'alrs': analysis['alrs'],\n"
            "    'game_over': analysis['result'].get('game_over'),\n"
            "    'game_over_ts': analysis['result'].get('game_over_ts'),\n"
            "    'skipped_entries_no_slot': analysis['result'].get("
            "'skipped_entries_no_slot'),\n"
            "}"
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
