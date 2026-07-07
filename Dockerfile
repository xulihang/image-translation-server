FROM python:3.11-slim

# 换 USTC 源
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.ustc.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|http://security.debian.org/debian-security|https://mirrors.ustc.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources

# ONNX Runtime + JavaFX headless 的系统库 + 多语言字体
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
    fonts-noto-cjk \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.ustc.edu.cn/pypi/web/simple -r requirements.txt

COPY server.py swagger.json ./
COPY templates/ templates/

RUN mkdir -p temp ImageTrans

EXPOSE 5000

CMD ["python", "server.py"]
