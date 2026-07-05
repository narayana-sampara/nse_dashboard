FROM postgres:16-alpine
COPY scripts/backup-postgres.sh /usr/local/bin/backup-postgres
RUN chmod 0555 /usr/local/bin/backup-postgres
ENTRYPOINT ["/bin/sh", "/usr/local/bin/backup-postgres"]
