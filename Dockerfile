FROM mambaorg/micromamba:2.0.5 AS base

COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml
RUN micromamba env update --yes --name base --file /tmp/environment.yml && \
	micromamba clean --all --yes
ENV PATH=/opt/conda/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/srv:/srv/server:/srv/agent
USER root
RUN mkdir -p /data /state /bootstrap /var/lib/labpc/sim && \
	chown -R $MAMBA_USER:$MAMBA_USER /data /state /bootstrap /var/lib/labpc
USER $MAMBA_USER

FROM base AS server
WORKDIR /srv
COPY --chown=$MAMBA_USER:$MAMBA_USER shared/ /srv/shared/
COPY --chown=$MAMBA_USER:$MAMBA_USER server/ /srv/server/
COPY --chown=$MAMBA_USER:$MAMBA_USER config_settings.toml /srv/config_settings.toml
ENV LAB_CONFIG_FILE=/srv/config_settings.toml
EXPOSE 8000
CMD ["uvicorn", "app.main:createApp", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]

FROM base AS agent
WORKDIR /srv
COPY --chown=$MAMBA_USER:$MAMBA_USER shared/ /srv/shared/
COPY --chown=$MAMBA_USER:$MAMBA_USER config_settings.toml /srv/config_settings.toml
ENV LAB_CONFIG_FILE=/srv/config_settings.toml
CMD ["python", "-c", "print('Agent implementation is scheduled for Stage 7')"]

FROM base AS tests
USER root
WORKDIR /srv
COPY . /srv/
RUN chown -R $MAMBA_USER:$MAMBA_USER /srv
USER $MAMBA_USER
ENV LAB_CONFIG_FILE=/srv/config_settings.toml
CMD ["pytest", "-q"]
