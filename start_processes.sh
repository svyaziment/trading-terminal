#!/usr/bin/env bash
set -u
export PYTHONUNBUFFERED=1
PRESERVE_PAPER=${PRESERVE_PAPER_PROCESSES:-0}

if [[ -n "${DURATION_MINUTES:-}" ]]; then
    DURATION="${DURATION_MINUTES}"
    SESSION_AWARE=0
    echo "=== Fixed duration: ${DURATION} min from launch (DURATION_MINUTES) ==="
else
    SESSION_AWARE=1
    echo "=== Session-aware duration: until next session open after 19:00 (stop/take after close) ==="
    DURATION=$(docker compose exec -T backend python -c "from app.analytics.moex_session import minutes_until_stack_end; print(minutes_until_stack_end())")
    DURATION=$(echo "${DURATION}" | tr -d '\r' | tr -d '[:space:]')
    if ! [[ "${DURATION}" =~ ^[0-9]+$ ]]; then
        echo "Failed to compute session duration. Rebuild backend first:"
        echo "  docker compose up -d --build backend"
        echo "Raw output: ${DURATION}"
        exit 1
    fi
    echo "Paper processes will run for ${DURATION} min"
fi

if [[ "${PRESERVE_PAPER}" == "1" ]]; then
    if [[ "${START_LIVE_EXECUTOR:-0}" != "1" ]]; then
        echo "PRESERVE_PAPER_PROCESSES=1 requires START_LIVE_EXECUTOR=1"
        exit 1
    fi
    echo "=== Steps 0-5: Preserve existing paper processes ==="
    docker compose exec -T backend python -c "
import os, sys
targets = {'run_data_refresher': 0, 'run_online_data': 0, 'run_live_engine': 0, 'run_paper_trader': 0}
for pid_dir in os.listdir('/proc'):
    if not pid_dir.isdigit(): continue
    pid = int(pid_dir)
    if pid == os.getpid(): continue
    try:
        with open(f'/proc/{pid_dir}/cmdline','rb') as f:
            cmd = f.read().decode('utf-8', errors='replace').replace('\x00',' ')
        for target in targets:
            if target in cmd: targets[target] += 1
    except: pass
for target, count in targets.items():
    print(f'{target}: {count}')
if any(count != 1 for count in targets.values()):
    print('Preflight failed: expected exactly one of each paper process')
    sys.exit(1)
print('Paper processes will remain running')
"
else
    echo "=== Step 0: Stop all existing processes ==="
    docker compose exec -T backend python -c "
import os, signal
targets = ['run_data_refresher', 'run_online_data', 'run_signal_engine', 'run_live_engine', 'run_paper_trader', 'run_levels_refresher', 'LiveExecutor']
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
    nohup docker compose exec -T backend python -u -c "import logging,sys; logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(asctime)s %(levelname)s %(message)s'); from app.analytics.data_refresher import run_data_refresher; run_data_refresher(duration_minutes=${DURATION})" > reports/data-refresher/refresher.log 2>&1 &
    sleep 1

    echo "=== Step 3: Start streaming (1min candles + order book) ==="
    mkdir -p reports/streaming
    nohup docker compose exec -T backend python -u -c "import logging,sys; logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(asctime)s %(levelname)s %(message)s'); from app.analytics.online_data import run_online_data; run_online_data(duration_minutes=${DURATION})" > reports/streaming/streaming.log 2>&1 &
    sleep 1

    echo "=== Step 4: Start live strategy engine (unified brain) ==="
    mkdir -p reports/live-engine
    nohup docker compose exec -T backend python -u -c "import logging,sys; logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(asctime)s %(levelname)s %(message)s'); from app.analytics.live_engine import run_live_engine; run_live_engine(duration_minutes=${DURATION})" > reports/live-engine/live.log 2>&1 &
    sleep 1

    echo "=== Step 5: Start paper trader ==="
    mkdir -p reports/paper-trader
    nohup docker compose exec -T backend python -u -c "import logging,sys; logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(asctime)s %(levelname)s %(message)s'); from app.analytics.paper_trader import run_paper_trader; run_paper_trader(duration_minutes=${DURATION})" > reports/paper-trader/trader.log 2>&1 &
    sleep 3
fi

echo "=== Step 6: Start sandbox live executor when explicitly requested ==="
if [[ "${START_LIVE_EXECUTOR:-0}" == "1" ]]; then
    mkdir -p reports/live-executor
    if [[ "${SESSION_AWARE}" == "1" ]]; then
        echo "LiveExecutor: until_session_end (entries 10:00-19:00 MSK, stop/take until flat)"
        nohup docker compose exec -T backend python -u -c "import logging,sys; logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(asctime)s %(levelname)s %(message)s'); from app.analytics.live_executor import LiveExecutor; LiveExecutor().run(until_session_end=True)" > reports/live-executor/executor.log 2>&1 &
    else
        echo "LiveExecutor: duration_minutes=${DURATION} from launch"
        nohup docker compose exec -T backend python -u -c "import logging,sys; logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(asctime)s %(levelname)s %(message)s'); from app.analytics.live_executor import LiveExecutor; LiveExecutor().run(duration_minutes=${DURATION})" > reports/live-executor/executor.log 2>&1 &
    fi
    sleep 1
else
    echo "Sandbox LiveExecutor disabled for this launch (set START_LIVE_EXECUTOR=1)"
fi

echo "=== Step 7: Verify background processes ==="
docker compose exec -T backend python -c "
import os
targets = {'run_data_refresher': 0, 'run_online_data': 0, 'run_live_engine': 0, 'run_paper_trader': 0}
if '${START_LIVE_EXECUTOR:-0}' == '1':
    targets['LiveExecutor'] = 0
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
if [[ "${SESSION_AWARE}" == "1" ]]; then
    echo "Paper duration=${DURATION} min; LiveExecutor entries 10:00-19:00 MSK, stop/take by price"
else
    echo "All processes started with duration=${DURATION} min"
fi
