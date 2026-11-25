FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS python-dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml constraints.txt ./
RUN python -c "import tomllib,subprocess; p=tomllib.load(open('pyproject.toml','rb')); subprocess.check_call(['pip','install','--no-cache-dir','-c','constraints.txt','setuptools>=75','wheel',*p['project']['dependencies']])"
RUN useradd --uid 10001 --create-home app && mkdir -p /var/lib/archive-desk/sessions && chown app:app /var/lib/archive-desk/sessions && chmod 700 /var/lib/archive-desk/sessions
RUN mkdir -p /var/lib/archive-desk/content && chown app:app /var/lib/archive-desk/content && chmod 700 /var/lib/archive-desk/content

FROM python-dependencies AS runtime
COPY app ./app
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS instagram-wheel
WORKDIR /build
COPY vendor/instagrapi-source.zip vendor/instagram-build.json ./
RUN python -c "import hashlib,json,zipfile; p=json.load(open('instagram-build.json')); assert hashlib.sha256(open('instagrapi-source.zip','rb').read()).hexdigest()==p['archive_sha256']; zipfile.ZipFile('instagrapi-source.zip').extractall('/build/source')"
RUN pip wheel --no-deps /build/source --wheel-dir /wheels

FROM python-dependencies AS instagram-dependencies
COPY --from=instagram-wheel /wheels /wheels
COPY vendor/instagram-build.json /app/instagram-build.json
COPY instagram-constraints.txt /app/instagram-constraints.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -c constraints.txt -c instagram-constraints.txt /wheels/instagrapi-*.whl && pip check && rm -r /wheels
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Keep dependency downloads cached when application code changes.
FROM instagram-dependencies AS instagram
COPY app ./app
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS whisper-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl cmake g++ make \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir /whisper \
    && curl --fail --location --retry 2 https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/2eeeba56e9edd762b4b38467bab96c2517163158 -o /tmp/source.tar.gz \
    && tar xzf /tmp/source.tar.gz -C /whisper --strip-components=1 \
    && cmake -S /whisper -B /whisper/build -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
    && cmake --build /whisper/build --target whisper-cli --parallel 2
RUN curl --fail --location --retry 2 https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.bin -o /whisper/base.bin \
    && echo "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe  /whisper/base.bin" | sha256sum -c -

FROM instagram-dependencies AS instagram-worker-dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY --from=whisper-build /whisper/build/bin/whisper-cli /usr/local/bin/whisper-cli
COPY --from=whisper-build /whisper/base.bin /opt/archive-whisper/base.bin

FROM instagram-worker-dependencies AS instagram-worker
COPY app ./app
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY alembic.ini ./
COPY migrations ./migrations
USER app
