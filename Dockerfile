# Plotwise as a hosted webpage (Hugging Face Spaces, Render, or any Docker host). See HOSTING.md.
FROM python:3.12-slim

# fonts the style guides ask for: Liberation = metric twins of Arial / Times New Roman
RUN apt-get update && apt-get install -y --no-install-recommends fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
RUN mkdir -p /home/user/app
WORKDIR /home/user/app

COPY --chown=user . .

# Uploading plotwise.zip next to this file is enough: a browser upload often leaves out folders, so the zip's
# contents are unpacked here and replace any loose copies (upload a newer zip to update the Space).
RUN if [ -f plotwise.zip ]; then z=plotwise.zip; else z=$(ls *.zip 2>/dev/null | head -n 1); fi; \
    if [ -n "$z" ]; then python -m zipfile -e "$z" /tmp/pw && cp -r /tmp/pw/*/. . && rm -rf /tmp/pw; fi; \
    test -f plotwise/core.py || { echo "The plotwise folder is missing: upload plotwise.zip to the Space (see HOSTING.md)."; exit 1; }

RUN pip install --no-cache-dir --user -r requirements.txt gunicorn

# PLOTWISE_HOSTED: unguessable session links, each browser sees only its own sessions,
# and the AI's plot code runs inside a file/network guard. Set ANTHROPIC_API_KEY (and optionally
# APP_PASSWORD) as secrets on the host, never in this file.
ENV PLOTWISE_HOSTED=1 PORT=7860 MPLBACKEND=Agg PYTHONUTF8=1
EXPOSE 7860
CMD gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 900 app:app
