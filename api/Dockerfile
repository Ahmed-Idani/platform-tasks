# One image, three programs:
#   /api            the REST API (Deployment)
#   /storage-init   applies the bucket lifecycle (Job)
#   /goose + /migrations   database migrations (Job)
FROM golang:1.26 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ENV CGO_ENABLED=0
RUN go build -trimpath -ldflags="-s -w" -o /out/api ./cmd/api \
 && go build -trimpath -ldflags="-s -w" -o /out/storage-init ./cmd/storage-init \
 && GOBIN=/out go install github.com/pressly/goose/v3/cmd/goose@v3.28.0

# distroless: no shell, no package manager, runs as non-root (uid 65532).
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ /
COPY migrations /migrations
EXPOSE 8080
ENTRYPOINT ["/api"]
