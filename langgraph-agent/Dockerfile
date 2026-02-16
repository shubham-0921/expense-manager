FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=300 -r requirements.txt
COPY . .
EXPOSE 7860
CMD ["python", "server.py"]
