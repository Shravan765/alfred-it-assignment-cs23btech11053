FROM alpine:3.21
RUN apk add --no-cache openssl
COPY gen.sh /gen.sh
RUN chmod 755 /gen.sh
ENTRYPOINT ["/gen.sh"]
