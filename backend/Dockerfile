FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

COPY backend/requirements.txt /tmp/requirements.txt

ARG TORCH_FLAVOR=cu118
ARG TORCH_VERSION=2.5.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install CLIP runtime deps at build time to avoid repeated install on container start.
# TORCH_FLAVOR supports: auto, cpu, cu118
RUN set -eux; \
    torch_cpu_filename="torch-${TORCH_VERSION}+cpu-cp312-cp312-linux_x86_64.whl"; \
    torch_cu118_filename="torch-${TORCH_VERSION}+cu118-cp312-cp312-linux_x86_64.whl"; \
    torch_cpu_url="https://download.pytorch.org/whl/cpu/torch-${TORCH_VERSION}%2Bcpu-cp312-cp312-linux_x86_64.whl"; \
    torch_cu118_url="https://download.pytorch.org/whl/cu118/torch-${TORCH_VERSION}%2Bcu118-cp312-cp312-linux_x86_64.whl"; \
    download_torch_wheel() { \
      url="$1"; \
      out="$2"; \
      rm -f "${out}"; \
      for n in 1 2 3 4 5; do \
        if curl -fL --retry 20 --retry-all-errors --connect-timeout 30 --speed-time 30 --speed-limit 10240 -C - -o "${out}" "${url}"; then \
          return 0; \
        fi; \
        echo "torch wheel download failed (attempt ${n}/5): ${url}"; \
        sleep 5; \
      done; \
      return 1; \
    }; \
    install_torch_cpu() { \
      out="/tmp/${torch_cpu_filename}"; \
      download_torch_wheel "${torch_cpu_url}" "${out}"; \
      pip install --default-timeout=1200 --retries 20 --no-cache-dir "${out}"; \
    }; \
    install_torch_cu118() { \
      out="/tmp/${torch_cu118_filename}"; \
      download_torch_wheel "${torch_cu118_url}" "${out}"; \
      pip install --default-timeout=1200 --retries 20 --no-cache-dir "${out}"; \
    }; \
    if [ "$TORCH_FLAVOR" = "cpu" ]; then \
      install_torch_cpu; \
    elif [ "$TORCH_FLAVOR" = "cu118" ]; then \
      ok=0; \
      for n in 1 2; do \
        if install_torch_cu118; then ok=1; break; fi; \
        echo "cu118 torch install failed (attempt ${n}/2), retrying..."; \
        sleep 5; \
      done; \
      if [ "$ok" -ne 1 ]; then \
        echo "cu118 install failed repeatedly; falling back to cpu torch."; \
        install_torch_cpu; \
      fi; \
    elif [ "$TORCH_FLAVOR" = "auto" ]; then \
      if ! install_torch_cu118; then \
        echo "auto mode: cu118 install failed; falling back to cpu torch."; \
        install_torch_cpu; \
      fi; \
    else \
      echo "Unsupported TORCH_FLAVOR: $TORCH_FLAVOR" && exit 1; \
    fi; \
    pip install --default-timeout=600 --retries 20 --no-cache-dir -r /tmp/requirements.txt; \
    rm -f /tmp/torch-*.whl

COPY backend /app/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
