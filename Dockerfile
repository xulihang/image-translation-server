FROM python:3.11-slim

# ONNX Runtime + JavaFX headless 需要的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libstdc++6 \
    libgtk-3-0 \
    libgl1 \
    libglib2.0-0 \
    libxtst6 \
    libxrender1 \
    libxi6 \
    libfreetype6 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py swagger.json ./
COPY templates/ templates/

RUN mkdir -p temp ImageTrans

EXPOSE 5000

CMD ["python", "server.py"]
