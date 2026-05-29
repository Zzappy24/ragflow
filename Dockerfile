# deps stage for Kaniko-compatible cross-stage COPY
FROM infiniflow/ragflow_deps:latest AS deps

# base stage
FROM ubuntu:24.04 AS base
USER root
SHELL ["/bin/bash", "-c"]

ARG NEED_MIRROR=0
ARG NGINX_VERSION=1.29.5-1~noble

WORKDIR /ragflow

ENV DEBIAN_FRONTEND=noninteractive
ENV TIKA_SERVER_JAR="file:///ragflow/tika-server-standard-3.3.0.jar"
ENV PYTHONDONTWRITEBYTECODE=1 \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    UV_HTTP_TIMEOUT=200 \
    UV_HTTP_RETRIES=3
ENV PATH=/root/.local/bin:$PATH

# Prepare directories
RUN mkdir -p /ragflow/rag/res/deepdoc /root/.ragflow /tmp/ragflow_deps

# Copy model/data artifacts from deps image
COPY --from=deps /huggingface.co/InfiniFlow/text_concat_xgb_v1.0 /tmp/ragflow_deps/InfiniFlow/text_concat_xgb_v1.0
COPY --from=deps /huggingface.co/InfiniFlow/deepdoc /tmp/ragflow_deps/InfiniFlow/deepdoc
COPY --from=deps /nltk_data /root/nltk_data
COPY --from=deps /tika-server-standard-3.3.0.jar /ragflow/tika-server-standard-3.3.0.jar
COPY --from=deps /tika-server-standard-3.3.0.jar.md5 /ragflow/tika-server-standard-3.3.0.jar.md5
COPY --from=deps /cl100k_base.tiktoken /ragflow/9b5ad71b2ce5302211f9c61530b329a4922fc6a4
COPY --from=deps /uv-x86_64-unknown-linux-gnu.tar.gz /tmp/uv-x86_64-unknown-linux-gnu.tar.gz
COPY --from=deps /uv-aarch64-unknown-linux-gnu.tar.gz /tmp/uv-aarch64-unknown-linux-gnu.tar.gz
COPY --from=deps /chrome-linux64-121-0-6167-85 /tmp/chrome-linux64.zip
COPY --from=deps /chromedriver-linux64-121-0-6167-85 /tmp/chromedriver-linux64.zip
COPY --from=deps /libssl1.1_1.1.1f-1ubuntu2_amd64.deb /tmp/libssl1.1_amd64.deb
COPY --from=deps /libssl1.1_1.1.1f-1ubuntu2_arm64.deb /tmp/libssl1.1_arm64.deb

RUN cp -r /tmp/ragflow_deps/InfiniFlow/text_concat_xgb_v1.0 /ragflow/rag/res/deepdoc/ && \
    cp -r /tmp/ragflow_deps/InfiniFlow/deepdoc /ragflow/rag/res/deepdoc/ && \
    rm -rf /tmp/ragflow_deps

# Setup apt base and optional mirror
RUN apt update && \
    apt install -y --no-install-recommends ca-certificates curl gnupg && \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|http://archive.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources && \
        sed -i 's|http://security.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources; \
    fi && \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    chmod 1777 /tmp && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

# Core dependencies
RUN apt update && \
    apt install -y --no-install-recommends \
      build-essential \
      libglib2.0-0 \
      libglx-mesa0 \
      libgl1 \
      pkg-config \
      libicu-dev \
      libgdiplus \
      default-jdk \
      libatk-bridge2.0-0 \
      libpython3-dev \
      libgtk-4-1 \
      libnss3 \
      xdg-utils \
      libgbm-dev \
      libjemalloc-dev \
      unzip \
      curl \
      wget \
      git \
      vim \
      less \
      ghostscript \
      pandoc \
      postgresql-client && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

# Heavy TeX/fonts packages isolated to reduce Kaniko memory pressure
RUN apt update && \
    apt install -y --no-install-recommends \
      texlive \
      texlive-latex-extra \
      texlive-xetex \
      texlive-lang-chinese \
      fonts-freefont-ttf \
      fonts-noto-cjk && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

# Download resource from GitHub/Gitee to /usr/share/infinity
RUN mkdir -p /usr/share/infinity/resource && \
    if [ "$NEED_MIRROR" == "1" ]; then \
        git clone --depth 1 --single-branch https://gitee.com/infiniflow/resource /tmp/resource; \
    else \
        git clone --depth 1 --single-branch https://github.com/infiniflow/resource.git /tmp/resource; \
    fi && \
    cp -r /tmp/resource/* /usr/share/infinity/resource && \
    rm -rf /tmp/resource

# Install nginx
RUN mkdir -p /etc/apt/keyrings && \
    curl --retry 5 --retry-delay 2 --retry-all-errors -fsSL https://nginx.org/keys/nginx_signing.key | gpg --dearmor -o /etc/apt/keyrings/nginx-archive-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nginx-archive-keyring.gpg] https://nginx.org/packages/mainline/ubuntu/ noble nginx" > /etc/apt/sources.list.d/nginx.list && \
    apt update && \
    apt install -y --no-install-recommends nginx=${NGINX_VERSION} && \
    apt-mark hold nginx && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN if [ "$NEED_MIRROR" == "1" ]; then \
        mkdir -p /etc/uv && \
        echo 'python-install-mirror = "https://registry.npmmirror.com/-/binary/python-build-standalone/"' > /etc/uv/uv.toml && \
        echo '[[index]]' >> /etc/uv/uv.toml && \
        echo 'url = "https://mirrors.aliyun.com/pypi/simple"' >> /etc/uv/uv.toml && \
        echo 'default = true' >> /etc/uv/uv.toml; \
    fi && \
    arch="$(uname -m)" && \
    if [ "$arch" = "x86_64" ]; then uv_arch="x86_64"; else uv_arch="aarch64"; fi && \
    tar xzf "/tmp/uv-${uv_arch}-unknown-linux-gnu.tar.gz" && \
    cp "uv-${uv_arch}-unknown-linux-gnu/"* /usr/local/bin/ && \
    rm -rf "uv-${uv_arch}-unknown-linux-gnu" "/tmp/uv-${uv_arch}-unknown-linux-gnu.tar.gz" && \
    uv python install 3.13

# nodejs 12.22 on Ubuntu 22.04 is too old
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt purge -y nodejs npm || true && \
    apt autoremove -y && \
    apt update && \
    apt install -y --no-install-recommends nodejs && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

# Add msssql ODBC driver
# macOS ARM64 environment, install msodbcsql18.
# general x86_64 environment, install msodbcsql17.
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl -fsSL https://packages.microsoft.com/config/ubuntu/22.04/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt update && \
    arch="$(uname -m)" && \
    if [ "$arch" = "arm64" ] || [ "$arch" = "aarch64" ]; then \
        ACCEPT_EULA=Y apt install -y --no-install-recommends unixodbc-dev msodbcsql18; \
    else \
        ACCEPT_EULA=Y apt install -y --no-install-recommends unixodbc-dev msodbcsql17; \
    fi || \
    { echo "Failed to install ODBC driver"; exit 1; } && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

# Add dependencies of selenium
RUN unzip /tmp/chrome-linux64.zip && \
    mv chrome-linux64 /opt/chrome && \
    ln -sf /opt/chrome/chrome /usr/local/bin/chrome && \
    rm -f /tmp/chrome-linux64.zip

RUN unzip -j /tmp/chromedriver-linux64.zip chromedriver-linux64/chromedriver && \
    mv chromedriver /usr/local/bin/ && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -f /usr/bin/google-chrome /tmp/chromedriver-linux64.zip

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
RUN if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|pypi.org|mirrors.aliyun.com/pypi|g' uv.lock; \
    else \
        sed -i 's|mirrors.aliyun.com/pypi|pypi.org|g' uv.lock; \
    fi && \
    uv sync --python 3.13 --frozen && \
    .venv/bin/python3 -m ensurepip --upgrade

COPY web web
COPY docs docs
RUN cd web && \
    NODE_OPTIONS="--max-old-space-size=8192" npm install && \
    NODE_OPTIONS="--max-old-space-size=8192" VITE_BUILD_SOURCEMAP=false VITE_MINIFY=esbuild npm run build

# Cyllene management panel frontend — same Vite/React build pattern as web/
# but smaller (admin UI). 4 GB heap is enough.
COPY management/web management/web
RUN --mount=type=cache,id=management_npm,target=/root/.npm,sharing=locked \
    cd management/web && NODE_OPTIONS="--max-old-space-size=4096" npm install && \
    NODE_OPTIONS="--max-old-space-size=4096" npm run build

COPY .git /ragflow/.git

RUN version_info=$(git describe --tags --match=v* --first-parent --always) && \
    echo "RAGFlow version: $version_info" && \
    echo "$version_info" > /ragflow/VERSION

# production stage
FROM base AS production
USER root

WORKDIR /ragflow

# Copy Python environment and packages
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
COPY management management
COPY common common
COPY memory memory
COPY bin bin

COPY docker/service_conf.yaml.template ./conf/service_conf.yaml.template
COPY docker/entrypoint.sh ./
RUN chmod +x ./entrypoint*.sh

# Copy nginx configuration for frontend serving
COPY docker/nginx/ragflow.conf.golang docker/nginx/ragflow.conf.python docker/nginx/ragflow.conf.hybrid docker/nginx/nginx.conf docker/nginx/proxy.conf /etc/nginx/
RUN mv /etc/nginx/ragflow.conf.golang /etc/nginx/conf.d/ragflow.conf.golang && \
    mv /etc/nginx/ragflow.conf.python /etc/nginx/conf.d/ragflow.conf.python && \
    mv /etc/nginx/ragflow.conf.hybrid /etc/nginx/conf.d/ragflow.conf.hybrid && \
    rm -f /etc/nginx/sites-enabled/default

# Copy compiled web pages
COPY --from=builder /ragflow/web/dist /ragflow/web/dist

# Management panel frontend (Cyllene). dist/ is gitignored, so the COPY
# management management above never carries it — the builder stage's npm
# run build is what produces the artefact we ship here.
COPY --from=builder /ragflow/management/web/dist /ragflow/management/web/dist

COPY --from=builder /ragflow/VERSION /ragflow/VERSION

# CUSTOM B2B SaaS — create non-root user (uid 10001) for future
# `runAsUser: 10001` adoption (Phase 2 hardening), and chown the paths
# nginx writes to. We deliberately DO NOT chown /ragflow itself: the K8s
# pods drop CAP_DAC_OVERRIDE via `capabilities.drop: ["ALL"]`, after
# which root is denied write access on paths it doesn't own — chown'ing
# /ragflow to 10001 would break tiktoken's cache write at startup. The
# venv stays root-owned and the runtime user (root by default in the
# upstream image) keeps full control of /ragflow.
RUN groupadd -g 10001 ragflow \
 && useradd -u 10001 -g 10001 -m -s /bin/bash ragflow \
 && chown -R 10001:10001 /var/log/nginx /var/cache/nginx /var/lib/nginx 2>/dev/null || true \
 && mkdir -p /var/run/nginx \
 && chown -R 10001:10001 /var/run/nginx

ENTRYPOINT ["./entrypoint.sh"]