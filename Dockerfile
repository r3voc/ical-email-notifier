# smallest possible  python docker image. copy requirements.txt, templates/ and main.py. make sure /app/data is created. use python 3.13
FROM python:3.13-slim AS base

# set working directory
WORKDIR /app

# install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS code

COPY main.py .
COPY templates/ ./templates/

# create data directory
RUN mkdir -p /app/data

FROM code AS runner

CMD ["python", "main.py"]