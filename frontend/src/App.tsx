import { useState } from "react";
import SignalsPanel from "./components/SignalsPanel";
import PatternStatsPanel from "./components/PatternStatsPanel";
import TopStocksPanel from "./components/TopStocksPanel";
import InstrumentsPanel from "./components/InstrumentsPanel";

type Tab = "signals" | "stats" | "top" | "instruments";

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "signals", label: "Сигналы" },
  { id: "stats", label: "Статистика" },
  { id: "top", label: "ТОП-30" },
  { id: "instruments", label: "Инструменты" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("signals");

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/70 px-6 py-4">
        <h1 className="text-xl font-semibold">Trading Terminal</h1>
        <p className="text-sm text-slate-400">
          MOEX analytics and trading signals
        </p>
      </header>

      <nav className="flex gap-2 border-b border-slate-800 px-6 py-3">
        {tabs.map((item) => (
          <button
            key={item.id}
            onClick={() => setTab(item.id)}
            className={
              "rounded px-3 py-1.5 text-sm font-medium transition " +
              (tab === item.id
                ? "bg-sky-500/20 text-sky-300"
                : "text-slate-400 hover:bg-slate-800 hover:text-slate-200")
            }
          >
            {item.label}
          </button>
        ))}
      </nav>

      <main className="p-6">
        {tab === "signals" && <SignalsPanel />}
        {tab === "stats" && <PatternStatsPanel />}
        {tab === "top" && <TopStocksPanel />}
        {tab === "instruments" && <InstrumentsPanel />}
      </main>
    </div>
  );
}
