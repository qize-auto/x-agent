FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY pyproject.toml README.md ./
COPY xagent/ ./xagent/

# 安装 Python 依赖
RUN pip install --no-cache-dir -e ".[dev]"

# 创建配置目录
RUN mkdir -p /root/.xagent

# 默认启动 CLI
ENTRYPOINT ["xagent"]
CMD ["chat"]
