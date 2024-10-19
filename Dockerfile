FROM python:3.10.14-slim

ENV TIME_ZONE=Asia/Shanghai
ENV TZ=Asia/Shanghai

RUN mkdir /app

COPY . /app

WORKDIR /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "/app/main.py"]
