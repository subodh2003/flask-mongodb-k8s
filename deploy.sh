#!/usr/bin/env bash
set -e
minikube addons enable metrics-server
kubectl apply -f k8s/00-secret.yaml -f k8s/01-mongo-pv-pvc.yaml \
  -f k8s/02-mongo-statefulset.yaml -f k8s/03-mongo-service.yaml
kubectl rollout status statefulset/mongodb --timeout=180s
kubectl apply -f k8s/04-flask-deployment.yaml -f k8s/05-flask-service.yaml -f k8s/06-flask-hpa.yaml
kubectl rollout status deployment/flask-app --timeout=120s
echo "App URL: $(minikube service flask-service --url)"
