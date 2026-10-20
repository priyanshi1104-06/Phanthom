# Phantom's Go sandbox: the official Go image with the standard library compiled in advance,
# so a fresh container builds a small program in under a second instead of ~9 s.
# Build from the repo root:  docker build -f sandbox/go.Dockerfile -t phantom-go .
FROM golang:1.25-bookworm
RUN GOCACHE=/gocache go build std && chmod -R a+rwX /gocache
ENV GOCACHE=/gocache GOFLAGS=-buildvcs=false HOME=/tmp CGO_ENABLED=0
