# The app has no dependencies — this image is just Python plus the source.
FROM python:3.12-slim

WORKDIR /app
COPY . .

# PORT is what the host routes to; DATA_DIR is the mounted volume where
# rooms_state.json and leads/ live so they survive a redeploy.
ENV PORT=8080 \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# -u keeps the startup banner and logs streaming rather than sitting in a buffer
CMD ["python3", "-u", "server.py"]
