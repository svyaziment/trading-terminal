#!/usr/bin/env bash
set -u
DURATION=${DURATION_MINUTES:-1200}

echo "=== Step 0: Stop all existing processes ==="
docker compose exec -T backend python -c "
import os, signal
targets = ['run_data_refresher', 'run_online_data', 'run_signal_engine', 'run_paper_trader', 'run_levels_refresher']
killed = []
for pid_dir in os.listdir('/proc'):
    if not pid_dir.isdigit(): continue
    pid = int(pid_dir)
    if pid == os.getpid(): continue
    try:
        with open(f'/proc/{pid_dir}/cmdline','rb') as f:
            cmd = f.read().decode('utf-8', errors='replace').replace('\x00',' ')
        if any(t in cmd for t in targets):
            os.kill(pid, signal.SIGTERM)
            killed.append((pid, cmd[:60]))
    except: pass
for pid, cmd in killed:
    print(f'Killed PID {pid}: {cmd}')
if not killed:
    print('No processes to kill')
"
sleep 3

echo "=== Step 1: Catch-up pending+open positions (fill/cancel pending, then stop/take) ==="
docker compose exec -T backend python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s %(levelname)s %(message)s')
from app.analytics.position_catchup import catch_up_positions
result = catch_up_positions()
print(f'CATCHUP_RESULT: {result}')
"

echo "=== Step 2: Start data refresher (MOEX 1min + aggregation + FIGI) ==="
mkdir -p reports/data-refresher
nohup docker compose exec -T backend python -c "from app.analytics.data_refresher import run_data_refresher; run_data_refresher(duration_minutes=${DURATION})" > reports/data-refresher/refresher.log 2>&1 &
sleep 1

echo "=== Step 3: Start streaming (1min candles + order book) ==="
mkdir -p reports/streaming
nohup docker compose exec -T backend python -c "from app.analytics.online_data import run_online_data; run_online_data(duration_minutes=${DURATION})" > reports/streaming/streaming.log 2>&1 &
sleep 1

echo "=== Step 4: Start signal engine ==="
mkdir -p reports/signal-engine
nohup docker compose exec -T backend python -c "from app.analytics.online_signals import run_signal_engine; run_signal_engine(duration_minutes=${DURATION})" > reports/signal-engine/signals.log 2>&1 &
sleep 1

echo "=== Step 5: Start paper trader ==="
mkdir -p reports/paper-trader
nohup docker compose exec -T backend python -c "from app.analytics.paper_trader import run_paper_trader; run_paper_trader(duration_minutes=${DURATION})" > reports/paper-trader/trader.log 2>&1 &
sleep 3

echo "=== Step 6: Verify (should be 1 each) ==="
docker compose exec -T backend python -c "
import os
targets = {'run_data_refresher': 0, 'run_online_data': 0, 'run_signal_engine': 0, 'run_paper_trader': 0}
for pid_dir in os.listdir('/proc'):
    if not pid_dir.isdigit(): continue
    pid = int(pid_dir)
    if pid == os.getpid(): continue
    try:
        with open(f'/proc/{pid_dir}/cmdline','rb') as f:
            cmd = f.read().decode('utf-8', errors='replace').replace('\x00',' ')
        for t in targets:
            if t in cmd: targets[t] += 1
    except: pass
for t,c in targets.items():
    status = 'OK' if c==1 else ('MISSING!' if c==0 else f'DUPLICATE({c})')
    print(f'{t}: {c} - {status}')
"
echo "All processes started with duration=${DURATION} min"
