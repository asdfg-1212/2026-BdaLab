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
RUN set -eux; \
    archive="hadoop-${HADOOP_VERSION}-lean.tar.gz"; \
    downloaded=0; \
    for base in "${HADOOP_MIRROR}" "${HADOOP_FALLBACK}"; do \
      rm -f /tmp/hadoop.tar.gz /tmp/hadoop.sha512; \
      if curl -fSL --retry 2 --connect-timeout 10 "${base}/hadoop-${HADOOP_VERSION}/${archive}" -o /tmp/hadoop.tar.gz \
        && curl -fSL --retry 2 --connect-timeout 10 "${base}/hadoop-${HADOOP_VERSION}/${archive}.sha512" -o /tmp/hadoop.sha512 \
        && python3 -c "import hashlib,pathlib,re; p=pathlib.Path('/tmp/hadoop.tar.gz'); actual=hashlib.sha512(p.read_bytes()).hexdigest(); expected=re.search(r'(?i)\b[0-9a-f]{128}\b',pathlib.Path('/tmp/hadoop.sha512').read_text()).group().lower(); assert actual==expected, 'Hadoop checksum mismatch'"; then \
        downloaded=1; \
        break; \
      fi; \
    done; \
    test "$downloaded" = 1; \
    tar -xzf /tmp/hadoop.tar.gz -C /opt \
    && mv "/opt/hadoop-${HADOOP_VERSION}" /opt/hadoop \
    && rm /tmp/hadoop.tar.gz /tmp/hadoop.sha512

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY processing ./processing
COPY frontend ./frontend
COPY config ./config
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
