# 利益商品AI（rakuten_finder）
FROM python:3.11-slim

WORKDIR /app

COPY requirements-rakuten.txt .
RUN pip install --no-cache-dir -r requirements-rakuten.txt

COPY rakuten_finder/ rakuten_finder/
COPY config/ config/
COPY data/mercari_prices.sample.csv data/

# 既定はダッシュボード起動。巡回は
#   docker compose run finder python -m rakuten_finder.cli run
CMD ["uvicorn", "rakuten_finder.dashboard:app", "--host", "0.0.0.0", "--port", "8000"]
