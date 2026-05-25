# deps stage for Kaniko-compatible cross-stage COPY
FROM infiniflow/ragflow_deps:latest AS deps

# base stage
FROM ubuntu:24.04 AS base
USER root
SHELL ["/bin/bash", "-c"]

ARG NEED_MIRROR=0

WORKDIR /ragflow

# copy models downloaded via download_deps.py
RUN mkdir -p /ragflow/rag/res/deepdoc /root/.ragflow

# Replace BuildKit mount with standard multi-stage COPY
COPY --from=deps /huggingface.co/InfiniFlow/text_concat_xgb_v1.0 /tmp/ragflow_deps/InfiniFlow/text_concat_xgb_v1.0
COPY --from=deps /huggingface.co/InfiniFlow/deepdoc /tmp/ragflow_deps/InfiniFlow/deepdoc

RUN mkdir -p /ragflow/rag/res/deepdoc && \
    cp -r /tmp/ragflow_deps/InfiniFlow/text_concat_xgb_v1.0 /ragflow/rag/res/deepdoc/ && \
    cp -r /tmp/ragflow_deps/InfiniFlow/deepdoc /ragflow/rag/res/deepdoc/ && \
    rm -rf /tmp/ragflow_deps

# https://github.com/chrismattmann/tika-python
# This is the only way to run python-tika without internet access. Without this set, the default is to check the tika version and pull latest every time from Apache.
COPY --from=deps /nltk_data /root/nltk_data
COPY --from=deps /tika-server-standard-3.3.0.jar /ragflow/tika-server-standard-3.3.0.jar
COPY --from=deps /tika-server-standard-3.3.0.jar.md5 /ragflow/tika-server-standard-3.3.0.jar.md5
COPY --from=deps /cl100k_base.tiktoken /ragflow/9b5ad71b2ce5302211f9c61530b329a4922fc6a4

ENV TIKA_SERVER_JAR="file:///ragflow/tika-server-standard-3.3.0.jar"
ENV DEBIAN_FRONTEND=noninteractive

# Setup apt
# Python package and implicit dependencies:
# opencv-python: libglib2.0-0 libglx-mesa0 libgl1
# python-pptx:   default-jdk                              tika-server-standard-3.3.0.jar
# selenium:      libatk-bridge2.0-0                       chrome-linux64-121-0-6167-85
# Building C extensions: libpython3-dev libgtk-4-1 libnss3 xdg-utils libgbm-dev
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    apt update && \
    apt --no-install-recommends install -y ca-certificates; \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|http://archive.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources; \
        sed -i 's|http://security.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources; \
    fi; \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    chmod 1777 /tmp && \
    apt update && \
    apt install -y \
    build-essential libglib2.0-0 libglx-mesa0 libgl1 pkg-config libicu-dev libgdiplus default-jdk libatk-bridge2.0-0 libpython3-dev libgtk-4-1 libnss3 xdg-utils libgbm-dev libjemalloc-dev gnupg unzip curl wget git vim less ghostscript pandoc texlive texlive-latex-extra texlive-xetex texlive-lang-chinese fonts-freefont-ttf fonts-noto-cjk postgresql-client

# Download resource from GitHub to /usr/share/infinity
RUN mkdir -p /usr/share/infinity/resource && \
    if [ "$NEED_MIRROR" == "1" ]; then \
        git clone --depth 1 --single-branch https://gitee.com/infiniflow/resource /tmp/resource; \
    else \
        git clone --depth 1 --single-branch https://github.com/infiniflow/resource.git /tmp/resource; \
    fi && \
    cp -r /tmp/resource/* /usr/share/infinity/resource && \
    rm -rf /tmp/resource

ARG NGINX_VERSION=1.29.5-1~noble
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    mkdir -p /etc/apt/keyrings && \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSL https://nginx.org/keys/nginx_signing.key | gpg --dearmor -o /etc/apt/keyrings/nginx-archive-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nginx-archive-keyring.gpg] https://nginx.org/packages/mainline/ubuntu/ noble nginx" > /etc/apt/sources.list.d/nginx.list && \
    apt -o Acquire::Retries=5 update && \
    apt -o Acquire::Retries=5 install -y nginx=${NGINX_VERSION} && \
    apt-mark hold nginx

# Install uv
COPY --from=deps /uv-x86_64-unknown-linux-gnu.tar.gz /tmp/uv-x86_64-unknown-linux-gnu.tar.gz
COPY --from=deps /uv-aarch64-unknown-linux-gnu.tar.gz /tmp/uv-aarch64-unknown-linux-gnu.tar.gz

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        mkdir -p /etc/uv && \
        echo 'python-install-mirror = "https://registry.npmmirror.com/-/binary/python-build-standalone/"' > /etc/uv/uv.toml && \
        echo '[[index]]' >> /etc/uv/uv.toml && \
        echo 'url = "https://mirrors.aliyun.com/pypi/simple"' >> /etc/uv/uv.toml && \
        echo 'default = true' >> /etc/uv/uv.toml; \
    fi; \
    arch="$(uname -m)"; \
    if [ "$arch" = "x86_64" ]; then uv_arch="x86_64"; else uv_arch="aarch64"; fi; \
    tar xzf "/tmp/uv-${uv_arch}-unknown-linux-gnu.tar.gz" && \
    cp "uv-${uv_arch}-unknown-linux-gnu/"* /usr/local/bin/ && \
    rm -rf "uv-${uv_arch}-unknown-linux-gnu" "/tmp/uv-${uv_arch}-unknown-linux-gnu.tar.gz" && \
    uv python install 3.13

ENV PYTHONDONTWRITEBYTECODE=1 DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    UV_HTTP_TIMEOUT=200 \
    UV_HTTP_RETRIES=3
ENV PATH=/root/.local/bin:$PATH

# nodejs 12.22 on Ubuntu 22.04 is too old
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt purge -y nodejs npm && \
    apt autoremove -y && \
    apt update && \
    apt install -y nodejs

# Add msssql ODBC driver
# macOS ARM64 environment, install msodbcsql18.
# general x86_64 environment, install msodbcsql17.
RUN --mount=type=cache,id=ragflow_apt,target=/var/cache/apt,sharing=locked \
    curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt update && \
    arch="$(uname -m)"; \
    if [ "$arch" = "arm64" ] || [ "$arch" = "aarch64" ]; then \
        ACCEPT_EULA=Y apt install -y unixodbc-dev msodbcsql18; \
    else \
        ACCEPT_EULA=Y apt install -y unixodbc-dev msodbcsql17; \
    fi || \
    { echo "Failed to install ODBC driver"; exit 1; }

# Add dependencies of selenium
COPY --from=deps /chrome-linux64-121-0-6167-85 /tmp/chrome-linux64.zip
COPY --from=deps /chromedriver-linux64-121-0-6167-85 /tmp/chromedriver-linux64.zip

RUN unzip /tmp/chrome-linux64.zip && \
    mv chrome-linux64 /opt/chrome && \
    ln -s /opt/chrome/chrome /usr/local/bin/ && \
    rm -f /tmp/chrome-linux64.zip

RUN unzip -j /tmp/chromedriver-linux64.zip chromedriver-linux64/chromedriver && \
    mv chromedriver /usr/local/bin/ && \
    rm -f /usr/bin/google-chrome /tmp/chromedriver-linux64.zip

COPY --from=deps /libssl1.1_1.1.1f-1ubuntu2_amd64.deb /tmp/libssl1.1_amd64.deb
COPY --from=deps /libssl1.1_1.1.1f-1ubuntu2_arm64.deb /tmp/libssl1.1_arm64.deb

RUN if [ "$(uname -m)" = "x86_64" ]; then \
        dpkg -i /tmp/libssl1.1_amd64.deb; \
    elif [ "$(uname -m)" = "aarch64" ]; then \
        dpkg -i /tmp/libssl1.1_arm64.deb; \
    fi && \
    rm -f /tmp/libssl1.1_amd64.deb /tmp/libssl1.1_arm64.deb


# builder stage
FROM base AS builder
USER root

WORKDIR /ragflow

# install dependencies from uv.lock file
COPY pyproject.toml uv.lock ./

# https://github.com/astral-sh/uv/issues/10462
# uv records index url into uv.lock but doesn't failover among multiple indexes
RUN --mount=type=cache,id=ragflow_uv,target=/root/.cache/uv,sharing=locked \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|pypi.org|mirrors.aliyun.com/pypi|g' uv.lock; \
    else \
        sed -i 's|mirrors.aliyun.com/pypi|pypi.org|g' uv.lock; \
    fi; \
    uv sync --python 3.13 --frozen && \
    .venv/bin/python3 -m ensurepip --upgrade

COPY web web
COPY docs docs
RUN --mount=type=cache,id=ragflow_npm,target=/root/.npm,sharing=locked \
    cd web && NODE_OPTIONS="--max-old-space-size=8192" npm install && \
    NODE_OPTIONS="--max-old-space-size=8192" VITE_BUILD_SOURCEMAP=false VITE_MINIFY=esbuild npm run build

COPY .git /ragflow/.git

RUN version_info=$(git describe --tags --match=v* --first-parent --always); \
    echo "RAGFlow version: $version_info"; \
    echo $version_info > /ragflow/VERSION

# production stage
FROM base AS production
USER root

WORKDIR /ragflow

ENV VIRTUAL_ENV=/ragflow/.venv
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

ENV PYTHONPATH=/ragflow/

COPY web web
COPY admin admin
COPY api api
COPY conf conf
COPY deepdoc deepdoc
COPY rag rag
COPY agent agent
COPY pyproject.toml uv.lock ./
COPY mcp mcp
COPY common common
COPY memory memory
COPY bin bin

COPY docker/service_conf.yaml.template ./conf/service_conf.yaml.template
COPY docker/entrypoint.sh ./
RUN chmod +x ./entrypoint*.sh

COPY docker/nginx/ragflow.conf.golang docker/nginx/ragflow.conf.python docker/nginx/ragflow.conf.hybrid docker/nginx/nginx.conf docker/nginx/proxy.conf /etc/nginx/
RUN mv /etc/nginx/ragflow.conf.golang /etc/nginx/conf.d/ragflow.conf.golang && \
    mv /etc/nginx/ragflow.conf.python /etc/nginx/conf.d/ragflow.conf.python && \
    mv /etc/nginx/ragflow.conf.hybrid /etc/nginx/conf.d/ragflow.conf.hybrid && \
    rm -f /etc/nginx/sites-enabled/default

COPY --from=builder /ragflow/web/dist /ragflow/web/dist
COPY --from=builder /ragflow/VERSION /ragflow/VERSION

RUN groupadd -g 10001 ragflow \
 && useradd -u 10001 -g 10001 -m -s /bin/bash ragflow \
 && chown -R 10001:10001 /var/log/nginx /var/cache/nginx /var/lib/nginx 2>/dev/null || true \
 && mkdir -p /var/run/nginx \
 && chown -R 10001:10001 /var/run/nginx

ENTRYPOINT ["./entrypoint.sh"]