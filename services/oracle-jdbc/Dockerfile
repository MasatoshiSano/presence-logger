# --- build stage: needs javac, so JDK ---
FROM eclipse-temurin:21-jdk AS build

# ojdbc11.jar is fetched from Maven Central at build time.
# Override via:
#   docker compose build --build-arg OJDBC_VERSION=21.11.0.0 oracle-jdbc
ARG OJDBC_VERSION=23.5.0.24.07
ARG OJDBC_URL=https://repo1.maven.org/maven2/com/oracle/database/jdbc/ojdbc11/${OJDBC_VERSION}/ojdbc11-${OJDBC_VERSION}.jar

WORKDIR /build

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    curl -fsSL "$OJDBC_URL" -o /build/ojdbc11.jar; \
    rm -rf /var/lib/apt/lists/*

COPY src/Main.java /build/Main.java
RUN javac -cp /build/ojdbc11.jar -d /build /build/Main.java

# --- runtime stage: smaller JRE, no compile tools ---
FROM eclipse-temurin:21-jre AS runtime

WORKDIR /app

# Copy just the compiled bits + driver jar; no build tools end up in the image.
COPY --from=build /build/ojdbc11.jar /app/ojdbc11.jar
COPY --from=build /build/Main.class /app/Main.class

ENV PORT=8086
EXPOSE 8086

# Healthcheck uses wget which is in the JRE base image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/healthz" || exit 1

RUN useradd -r -s /usr/sbin/nologin -d /nonexistent jdbcuser
USER jdbcuser

ENTRYPOINT ["java", "-cp", "/app:/app/ojdbc11.jar", "Main"]
