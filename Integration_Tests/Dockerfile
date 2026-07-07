FROM python:3.14.6-slim

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY Integration_Tests/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./Integration_Tests .

EXPOSE 9090

USER app

CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "9090"]
