"""Build the executable notebook for Issue #130."""

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
            "# Issue #130 — портфель 50k `levels_sr_support`\n"
            "Кандидаты isolated C из #129 (`levels_sr_support` + `signal_4h_buy`) "
            "на общем капитале 50,000 RUB по слотам #44/#103. Ноутбук загружает "
            "прогон, проверяет SHA конфига и слоты, строит метрики и воспроизводит "
            "артефакты отчёта. Exclusive B-support 3811/1.51 и #124 B-mix не смешиваются с C."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "import sys\n"
            "from IPython.display import display, Markdown, Image\n\n"
            "ANALYSIS_DIR = Path.cwd()\n"
            "if not (ANALYSIS_DIR / 'analysis.py').exists():\n"
            "    ANALYSIS_DIR = Path("
            "'analytics/issue-130-sr-support-portfolio').resolve()\n"
            "sys.path.insert(0, str(ANALYSIS_DIR))\n"
            "from analysis import run_analysis, DISPLAY_NAME"
        ),
        nbf.v4.new_markdown_cell(
            "## Расчёт\n"
            "`run_analysis()` валидирует SHA конфига C, слоты 50k/10k/max 5, "
            "n кандидатов 4380 и покрытие 2026-08-20. Daily equity — по закрытым "
            "сделкам (без mark-to-market). Бар ALRS paper #711 во входах — блокер."
        ),
        nbf.v4.new_code_cell("analysis = run_analysis()\nanalysis['metrics']"),
        nbf.v4.new_markdown_cell("## Конфиг и вселенная"),
        nbf.v4.new_code_cell(
            "result = analysis['result']\n"
            "{\n"
            "    'strategy_config_name': result.get('strategy_config_name'),\n"
            "    'config_sha256': result.get('config_sha256'),\n"
            "    'plugin': result.get('strategy'),\n"
            "    'in_paper_test': result.get('in_paper_test'),\n"
            "    'locked': result.get('locked'),\n"
            "    'date_from': result.get('date_from'),\n"
            "    'date_to': result.get('date_to'),\n"
            "    'period_last_day': result.get('period_last_day'),\n"
            "    'candidate_trades': result.get('candidate_trades'),\n"
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
        nbf.v4.new_markdown_cell("## Сравнение книг, ALRS и GAME OVER"),
        nbf.v4.new_code_cell(
            "{\n"
            "    'verdict': analysis['verdict'],\n"
            "    'alrs': analysis['alrs'],\n"
            "    'split': analysis['split'],\n"
            "    'game_over': analysis['result'].get('game_over'),\n"
            "    'game_over_ts': analysis['result'].get('game_over_ts'),\n"
            "    'skipped_entries_no_slot': analysis['result'].get("
            "'skipped_entries_no_slot'),\n"
            "    'issue44': analysis['summary'].get('issue44_context'),\n"
            "    'issue103': analysis['summary'].get('issue103_context'),\n"
            "    'issue124_bmix': analysis['summary'].get('issue124_bmix'),\n"
            "}"
        ),
        nbf.v4.new_markdown_cell("## Итоговый отчёт"),
        nbf.v4.new_code_cell(
            "display(Markdown((ANALYSIS_DIR / 'report.md').read_text(encoding='utf-8')))"
        ),
        nbf.v4.new_code_cell(
            "display(Image(filename=str(ANALYSIS_DIR / 'plots/metrics_comparison.png')))"
        ),
    ]
    output = HERE / "analysis.ipynb"
    nbf.write(notebook, output)
    return output


def execute(path: Path | None = None) -> Path:
    import nbformat
    from nbclient import NotebookClient

    notebook_path = Path(path or HERE / "analysis.ipynb")
    notebook = nbformat.read(notebook_path, as_version=4)
    client = NotebookClient(
        notebook,
        timeout=180,
        kernel_name="python3",
        resources={"metadata": {"path": str(HERE)}},
    )
    client.execute()
    nbformat.write(notebook, notebook_path)
    return notebook_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build Issue #130 notebook")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    path = build()
    if args.execute:
        path = execute(path)
    print(path)
