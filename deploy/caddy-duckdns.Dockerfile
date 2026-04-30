FROM caddy:2-builder AS builder

RUN xcaddy build \
	--with github.com/caddy-dns/duckdns@v0.5.0

FROM caddy:2

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
