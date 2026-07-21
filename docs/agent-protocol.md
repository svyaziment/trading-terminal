# Agent Protocol

We use step-by-step task execution.

Each task:
1. Has a unique task ID.
2. Has a bash script.
3. Produces artifacts.
4. Produces checks.
5. Produces report.json.
6. Produces report.md.
7. Produces log.txt.

Primary feedback format:
- reports/<task_id>/report.json

Human-readable feedback:
- reports/<task_id>/report.md

Debug feedback:
- reports/<task_id>/log.txt
