FROM python:3.12-slim

# Java (JVM pour r5py) et osmium-tool (extraction OSM), cf.
# src/build_data_agglo.py::osm_pbf_creator et views/accessibilite_index.py.
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jdk-headless \
    osmium-tool \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/default-java

# Convention Hugging Face Spaces : exécuter en utilisateur non-root (UID 1000).
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR /home/user/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

# Port par défaut attendu par le SDK Docker de Hugging Face Spaces.
EXPOSE 7860

CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableXsrfProtection=false"]
