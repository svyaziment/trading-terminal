FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

RUN pip install --no-cache-dir -r requirements-dev.txt

COPY app ./app
COPY tests ./tests

RUN find /app -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
RUN find /app -type f -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

RUN python -c "from app.main import app; routes={getattr(route, 'path', '') for route in app.routes}; assert '/api/instruments' in routes, 'Missing /api/instruments: ' + str(sorted(routes))"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
