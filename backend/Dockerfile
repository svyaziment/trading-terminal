FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

# Основной индекс PyPI + приватный индекс T-Bank для t-tech-investments
RUN pip install --no-cache-dir \
    --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple \
    --trusted-host opensource.tbank.ru \
    -r requirements-dev.txt

COPY app ./app
COPY tests ./tests

# Чистка артефактов, которые могли попасть с хоста (Windows CRLF, __pycache__)
RUN find /app -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
RUN find /app -type f -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# Build-time smoke check: SDK должен импортироваться
RUN python -c "import t_tech.invest; from t_tech.invest.constants import INVEST_GRPC_API; print('SDK_OK')"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
