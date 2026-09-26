#!/usr/bin/env bash
# The platform on a local k3s cluster (k3d: k3s in Docker).
#
#   scripts/k8s.sh up        create the registry + a 3-node cluster (idempotent)
#   scripts/k8s.sh build     build api/web/worker images, push them, pin the new tag in kustomization.yaml
#   scripts/k8s.sh deploy    apply the manifests, run the Jobs, wait for every rollout
#   scripts/k8s.sh status    pods, jobs, queue depths
#   scripts/k8s.sh all       up + build + deploy
#   scripts/k8s.sh down      delete the cluster (images stay in the registry)
#
# Never touches your current kubectl context: everything uses --context k3d-platform.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CLUSTER=platform
CTX=k3d-$CLUSTER
NS=platform
REG_NAME=platform-registry.localhost
REG_PUSH=localhost:5050                      # how the host pushes (Docker allows plain HTTP to localhost)
REG_PULL=k3d-$REG_NAME:5050                  # how the cluster pulls
K="kubectl --context $CTX -n $NS"
KUSTOMIZATION=deploy/k8s/kustomization.yaml

bold=$'\e[1m'; reset=$'\e[0m'
say() { echo "${bold}==>${reset} $*"; }

cmd_up() {
  command -v k3d >/dev/null || { echo "k3d missing: https://k3d.io (single binary)"; exit 1; }
  if ! k3d registry list 2>/dev/null | grep -q "k3d-$REG_NAME"; then
    say "creating registry $REG_NAME on :5050"
    k3d registry create "$REG_NAME" --port 5050
  fi
  if ! k3d cluster list 2>/dev/null | grep -q "^$CLUSTER "; then
    say "creating cluster $CLUSTER (1 server + 2 agents, ingress on :8088)"
    k3d cluster create "$CLUSTER" --servers 1 --agents 2 \
      --registry-use "k3d-$REG_NAME:5050" -p "8088:80@loadbalancer" \
      --kubeconfig-update-default --kubeconfig-switch-context=false --wait
  fi
  kubectl --context "$CTX" get nodes
}

cmd_build() {
  local tag
  tag="$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- api worker web || echo "-dirty-$(date +%H%M%S)")"
  for c in api web worker; do
    say "building platform-$c:$tag"
    docker build -q -t "$REG_PUSH/platform-$c:$tag" "$c" >/dev/null
    docker push -q "$REG_PUSH/platform-$c:$tag" >/dev/null
    # Pin the tag the cluster will pull (a new tag = a rolling update on the next deploy).
    sed -i "/name: platform-$c\$/{n;n;s/newTag: .*/newTag: $tag/}" "$KUSTOMIZATION"
  done
  grep -A2 "name: platform-" "$KUSTOMIZATION" | grep -E "name:|newTag"
}

cmd_deploy() {
  [[ -f deploy/k8s/secrets.env ]] || { cp deploy/k8s/secrets.env.example deploy/k8s/secrets.env; say "created deploy/k8s/secrets.env from the example"; }
  # Jobs are immutable: delete the finished ones so this deploy runs them again.
  $K delete job migrate storage-init --ignore-not-found >/dev/null 2>&1 || true
  say "applying manifests"
  kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/k8s | kubectl --context "$CTX" apply -f -
  say "waiting for Postgres, RabbitMQ, Garage"
  $K rollout status statefulset/postgres --timeout=180s
  $K rollout status statefulset/rabbitmq --timeout=300s
  $K rollout status statefulset/garage --timeout=180s
  say "waiting for the migration and storage-init Jobs"
  $K wait --for=condition=complete job/migrate job/storage-init --timeout=300s
  say "waiting for api, web, inbox, worker"
  for d in api web inbox worker; do $K rollout status "deployment/$d" --timeout=600s; done
  cmd_status
  cat <<EOF

  ${bold}Web UI${reset}          http://localhost:8088          (or http://platform.localhost:8088)
  ${bold}Webhook inbox${reset}   http://inbox.localhost:8088    callback_url from the cluster: http://inbox.platform:9000/hook
  ${bold}RabbitMQ${reset}        http://rabbitmq.localhost:8088 (app / app)
EOF
}

cmd_status() {
  $K get pods -o wide | cut -c1-140
  echo
  $K get jobs
  echo
  $K exec rabbitmq-0 -- rabbitmqctl list_queues -q name messages consumers 2>/dev/null || true
}

cmd_down() {
  say "deleting cluster $CLUSTER (registry and its images are kept)"
  k3d cluster delete "$CLUSTER"
}

case "${1:-}" in
  up) cmd_up ;;
  build) cmd_build ;;
  deploy) cmd_deploy ;;
  status) cmd_status ;;
  all) cmd_up; cmd_build; cmd_deploy ;;
  down) cmd_down ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
