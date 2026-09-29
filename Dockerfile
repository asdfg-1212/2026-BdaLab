FROM eclipse-temurin:11-jre-jammy

ARG HADOOP_VERSION=3.4.2
ARG UBUNTU_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG HADOOP_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/apache/hadoop/common
ARG HADOOP_FALLBACK=https://dlcdn.apache.org/hadoop/common
ENV HADOOP_HOME=/opt/hadoop \
    PATH=/opt/hadoop/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    UV_DEFAULT_INDEX=${PYPI_INDEX_URL}

RUN sed -i \
    -e "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    -e "s|http://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
# 复用 Jammy 自带的 Python，避免再下载一份 Python 运行时。
# uv 从清华 PyPI 获取，避免额外拉取构建镜像。
RUN python3 -m pip install --no-cache-dir --index-url "${PYPI_INDEX_URL}" "uv==0.12.7" \
    && uv venv --python python3 /opt/venv
ENV PATH=/opt/venv/bin:/opt/hadoop/bin:$PATH
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN --mount=type=cache,id=movielens-hadoop-download,target=/var/cache/hadoop,sharing=locked set -eux; \
    cd /var/cache/hadoop; \
    archive="hadoop-${HADOOP_VERSION}-lean.tar.gz"; \
    curl -fSL --retry 3 --retry-all-errors --connect-timeout 10 --max-time 60 "${HADOOP_FALLBACK}/hadoop-${HADOOP_VERSION}/${archive}.sha512" -o "${archive}.sha512"; \
    downloaded=0; \
    for base in "${HADOOP_MIRROR}" "${HADOOP_FALLBACK}"; do \
      for attempt in 1 2 3; do \
        if sha512sum --check "${archive}.sha512"; then downloaded=1; break 2; fi; \
        if curl -fSL --continue-at - --connect-timeout 10 --speed-limit 1024 --speed-time 60 "${base}/hadoop-${HADOOP_VERSION}/${archive}" -o "${archive}"; then \
          if sha512sum --check "${archive}.sha512"; then downloaded=1; break 2; fi; \
          rm -f "${archive}"; \
        fi; \
        sleep 2; \
      done; \
    done; \
    test "$downloaded" = 1; \
    tar -xzf "${archive}" -C /opt \
    && mv "/opt/hadoop-${HADOOP_VERSION}" /opt/hadoop

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY processing ./processing
COPY frontend ./frontend
COPY config ./config
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
