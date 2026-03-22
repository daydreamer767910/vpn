FROM golang:1.22-alpine AS builder

WORKDIR /app

RUN go mod init app

# 再复制源码
COPY . .

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -o app .

FROM alpine:3.19

WORKDIR /app

COPY --from=builder /app/app /app/app

EXPOSE 5000
ENTRYPOINT ["/app/app"]
