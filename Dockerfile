# ============================================================================
# DevAgent v3.4 — Multi-stage Docker Build
# ============================================================================
# Build:  docker build -t devagent:3.4 .
# Run:    docker run -p 8911:8911 -e DEEPSEEK_API_KEY=sk-xxx devagent:3.4
#
# Features:
#   - Python 3.12 + Node.js 22 (for Mermaid diagram rendering)
#   - ruff + mypy + bandit + coverage (code quality & evaluation)
#   - FastAPI + WebSocket (API server)
#   - PlantUML support via kroki.io (no local JVM needed)
#   - Non-root user, healthcheck, proper signal handling
# ============================================================================

# ============================================================================
# Stage 1: Base — Python + Node.js runtime
# ============================================================================
FROM python:3.12-slim AS base

# Configure apt to use Aliyun mirrors (more reliable in China) + retry on failure
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::http::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::https::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries

# Install system dependencies + Node.js for Mermaid CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Configure npm mirror + retry for China network
RUN npm config set registry https://registry.npmmirror.com \
    && npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000

# Configure Puppeteer to use mirror for Chromium download (mermaid-cli depends on puppeteer)
ENV PUPPETEER_DOWNLOAD_BASE_URL=https://npmmirror.com/mirrors

# Install Mermaid CLI globally (for diagram rendering)
RUN npm install -g @mermaid-js/mermaid-cli@11 \
    && mmdc --version

WORKDIR /app

# ============================================================================
# Stage 2: Dependencies — install Python packages
# ============================================================================
FROM base AS deps

# Install build tools for compiling Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt pyproject.toml setup.py ./

# Install core + all optional dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
    fastapi>=0.104.0 \
    uvicorn[standard]>=0.24.0 \
    pydantic>=2.0.0 \
    websockets>=12.0 \
    requests>=2.28.0 \
    pyyaml>=6.0 \
    pytest>=7.0.0 \
    pytest-cov>=5.0 \
    coverage>=7.0 \
    ruff>=0.1.0 \
    mypy>=1.0 \
    bandit>=1.7 \
    pillow>=10.0

# ============================================================================
# Stage 3: Runtime — production image
# ============================================================================
FROM base AS runtime

# Create non-root user
RUN useradd -m -s /bin/bash devagent \
    && mkdir -p /app/outputs /app/.devagent /app/example \
    && chown -R devagent:devagent /app

# Copy Python packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=deps /usr/local/bin/ /usr/local/bin/

# Copy application code
COPY devagent/ /app/devagent/
COPY setup.py README.md pyproject.toml /app/
COPY example/ /app/example/
COPY docs/ /app/docs/

# Install DevAgent in editable mode (so imports work)
RUN pip install -e /app/ --no-deps

# Copy default configuration template
COPY devagent/configs/ /app/devagent/configs/

# Expose API port
EXPOSE 8911

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8911/health')" || exit 1

# Run as non-root
USER devagent

# Default: start API server
CMD ["devagent-api", "--host", "0.0.0.0", "--port", "8911"]
