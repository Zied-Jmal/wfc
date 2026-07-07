FROM python:3.14.6-slim

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY Dashboard_Repo/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wfc_shared /wfc_shared
RUN pip install -e /wfc_shared

COPY ./Dashboard_Repo .

EXPOSE 8080 8081

USER app

CMD ["python", "main.py"]
