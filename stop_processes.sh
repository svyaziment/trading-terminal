#!/usr/bin/env bash
set -u

echo "=== Step 0: Stop all existing processes ==="
docker compose exec -T backend python -c "
import os, signal
targets = ['run_data_refresher', 'run_online_data', 'run_signal_engine', 'run_paper_trader', 'run_levels_refresher']
killed = []
for pid_dir in os.listdir('/proc'):
    if not pid_dir.isdigit():
        continue
    pid = int(pid_dir)
    if pid == os.getpid():
        continue
    try:
        with open(f'/proc/{pid_dir}/cmdline', 'rb') as f:
            cmdline = f.read().decode('utf-8', errors='replace').replace('\x00', ' ')
        if any(t in cmdline for t in targets):
            os.kill(pid, signal.SIGTERM)
            killed.append((pid, cmdline[:60]))
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
for pid, cmd in killed:
    print(f'Killed PID {pid}: {cmd}')
if not killed:
    print('No processes to kill')
"
sleep 3