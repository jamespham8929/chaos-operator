# The operator image. Build it with `docker build -t chaos-operator .` and load
# it into kind with `kind load docker-image chaos-operator`.
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY chaos_operator ./chaos_operator
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.13-slim
COPY --from=build /install /usr/local
# k8s/operator-deployment.yaml runs as uid 65532 with a read-only root. The
# user needs a passwd entry, because kopf reads the user name at startup.
RUN useradd --uid 65532 --user-group --no-create-home --shell /usr/sbin/nologin chaos
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["kopf", "run", "--standalone", "--all-namespaces", "-m", "chaos_operator.operator"]
