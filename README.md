# Flask + MongoDB on Kubernetes (Minikube)

This is my solution for the FarAlpha intern task. A Flask app (2 to 5 pods, autoscaled on CPU) talks to a MongoDB StatefulSet that has authentication turned on and keeps its data on a persistent volume. I ran everything on Minikube (docker driver, Ubuntu 24.04, Minikube 1.38.1, Kubernetes 1.35).

## Files

```
app.py, requirements.txt, Dockerfile, deploy.sh
k8s/00-secret.yaml            credentials
k8s/01-mongo-pv-pvc.yaml      PV + PVC for MongoDB
k8s/02-mongo-statefulset.yaml MongoDB with auth
k8s/03-mongo-service.yaml     ClusterIP + headless service
k8s/04-flask-deployment.yaml  Flask, 2 replicas
k8s/05-flask-service.yaml     NodePort
k8s/06-flask-hpa.yaml         autoscaler
k8s/load-generator.yaml       only used for the load test
screenshots/
```

## Part 1: why use a virtual environment

A venv gives each project its own copy of the python packages. So two projects can use different versions of Flask without breaking each other, and I don't touch the system python. Anyone can rebuild the same setup from requirements.txt, and if the environment gets messy I just delete the folder and create it again.

For Part 1 I ran Flask locally against a MongoDB docker container (with a root user set, since the task needs auth later) and checked `/` and `/data` with curl before moving to Kubernetes.

## Changes I made to the given Flask code

- The PDF text had `name` / `main` without the underscores, fixed to `__name__` and `"__main__"`.
- The Mongo URI now has a username and password (`authSource=admin`). It comes from the `MONGODB_URI` environment variable, which is filled from a Kubernetes Secret.
- In the container I run it with gunicorn instead of the Flask development server.

## How to build, push and deploy

Needs Docker, Minikube, kubectl and a Docker Hub account.

Build and push the image (my Docker Hub name is `bashmonk`, use yours):
```bash
docker build -t <dockerhub-username>/flask-mongo-app:1.0 .
docker login
docker push <dockerhub-username>/flask-mongo-app:1.0
```
The YAML in `k8s/04-flask-deployment.yaml` already points to my image (`bashmonk/flask-mongo-app:1.0`). If you build your own image, change the `image:` line there to your name. (If you don't want to push, `minikube image load <image>` also works.)

Start the cluster and deploy:
```bash
minikube start
./deploy.sh
```
The script turns on the metrics-server addon (the autoscaler needs it), applies the YAML files in order, waits for MongoDB to be ready, then applies the Flask part. Doing it by hand is the same thing: apply 00 to 03, wait for `mongodb-0`, then apply 04, 05 and 06.

Check that it works. The URL comes from `minikube service flask-service --url`, for me it was `http://192.168.49.2:30080`:
```bash
curl http://192.168.49.2:30080/
curl -X POST -H "Content-Type: application/json" -d '{"sampleKey":"sampleValue"}' http://192.168.49.2:30080/data
curl http://192.168.49.2:30080/data
kubectl get pods,svc,pv,pvc,hpa
```

## What each resource does

| Resource | What it is for |
|---|---|
| Secret `mongo-secret` | root user, password and the connection string |
| PV `mongo-pv` + PVC `mongo-pvc` | 1Gi hostPath volume, mounted at `/data/db` (where mongod keeps its files) |
| StatefulSet `mongodb` | 1 replica, auth enabled with `MONGO_INITDB_ROOT_USERNAME/PASSWORD` |
| Service `mongodb-service` | ClusterIP, only reachable inside the cluster, Flask uses this |
| Service `mongodb-headless` | headless service the StatefulSet needs for a stable pod name |
| Deployment `flask-app` | 2 replicas, readiness probe, requests and limits |
| Service `flask-service` | NodePort 30080 so I can reach the app from my laptop |
| HPA `flask-hpa` | scales on CPU, target 70%, min 2, max 5 |

## DNS inside the cluster

CoreDNS gives every Service a name like `<service>.<namespace>.svc.cluster.local`. Every pod's `/etc/resolv.conf` points to CoreDNS and has the namespace in its search list, so inside the same namespace the short name `mongodb-service` is enough.

That is how Flask finds the database. The connection string is `mongodb://admin:<password>@mongodb-service:27017/?authSource=admin`. CoreDNS turns `mongodb-service` into the Service's ClusterIP, and kube-proxy sends the traffic to the MongoDB pod. Pod IPs change when a pod restarts but the Service name stays the same, so nothing in the code has an IP address in it. I checked it from inside a Flask pod and the name resolved to the ClusterIP of the service (`09-dns.png`).

## Resource requests and limits

The request is what the scheduler reserves for the pod. A pod only lands on a node that still has that much free. The limit is the maximum it may use. If a container goes over its CPU limit it gets throttled, and if it goes over the memory limit it is killed (OOMKilled). Without them one busy pod can eat the whole node and starve the others, and the scheduler has nothing to plan with.

The task gave the values, so both Flask and MongoDB use request 200m CPU / 250M memory and limit 500m CPU / 500M memory. One more reason the request matters here: the HPA percentage is calculated against the CPU request. So 70% means about 140m per pod, and that is why my pods reached 115% at around 230m.

## Why I chose this setup

- StatefulSet for MongoDB, not a Deployment, because a database wants a stable name and its own storage. A Deployment with one replica and a PVC would also run, but I went with the StatefulSet. A replica set with several Mongo pods is more than this task needs.
- A normal PV and PVC with hostPath, because the task asks for PV and PVC. On a real cluster I would use a storage class and volumeClaimTemplates. hostPath is fine on a single node Minikube.
- ClusterIP for MongoDB so it can't be reached from outside. I did not use NodePort or LoadBalancer for it for that reason.
- NodePort for Flask, the simplest way to reach it from my laptop. Ingress would be nicer but needs another addon.
- Credentials in a Secret so they are not in the image or the code. A Secret is only base64, not encrypted, and the password in this repo is just a demo one. In a real setup I would use something like sealed-secrets.
- gunicorn instead of the Flask dev server.
- HPA with `autoscaling/v2` on CPU at 70%, min 2, max 5, as asked.

## Testing

### Auth and persistence

- Without a password, `kubectl exec mongodb-0 -- mongosh --eval ...` fails with "command find requires authentication". With `-u admin -p ... --authenticationDatabase admin` it returns the data. Flask keeps working. (`08-auth.png`)
- I deleted the pod with `kubectl delete pod mongodb-0`, waited for it to come back, and `/data` still returned the same documents. (`07-persistence.png`)

### Autoscaling with high traffic

I started 4 busybox pods (`k8s/load-generator.yaml`) that call `/` and `/data` on `flask-service` in a loop, and watched in three terminals:

```bash
kubectl get hpa flask-hpa -w
kubectl get pods -l app=flask-app -w
kubectl top pods
```
To stop the load: `kubectl delete -f k8s/load-generator.yaml`.

Results:

| Stage | CPU (target 70%) | Replicas | Screenshot |
|---|---|---|---|
| Before load | 1% | 2 | 03 |
| Under load | 107% to 115% (about 225-230m per Flask pod) | scaled 2 -> 4 -> 5 | 04, 04b, 04c, 05, 10 |
| After I stopped the load | ~0% | stayed at 5 for a while | `describe hpa` showed ScaleDownStabilized |
| A few minutes later | 1% | back to 2 on its own | 06, 10 |

At 5 replicas the CPU was still above 70%, so the HPA said `TooManyReplicas`, meaning it wanted more but max is 5. The load generator pods used around 350m each and MongoDB about 160m while this ran.

The events from `kubectl describe hpa flask-hpa`:
```
Warning  FailedGetResourceMetric   50m  unable to get metrics for resource cpu: no metrics returned from resource metrics API
Warning  FailedGetResourceMetric   49m  did not receive metrics for targeted pods (pods might be unready)
Normal   SuccessfulRescale         28m  New size: 4; reason: cpu resource utilization (percentage of request) above target
Normal   SuccessfulRescale         27m  New size: 5; reason: cpu resource utilization (percentage of request) above target
Normal   SuccessfulRescale         13m  New size: 2; reason: All metrics below target
```
(the ages are from when I ran the command)

### Screenshots

- `01-pods-services.png`: pods, services, PV/PVC and HPA after deploy
- `02-curl-tests.png`: GET /, POST /data, GET /data
- `03-hpa-before.png`: idle, 1% and 2 replicas
- `04-hpa-cpu-above-target.png`: 115%/70% with 5 replicas, `kubectl top pods`
- `04b-hpa-watch.png`: `kubectl get hpa -w` under load
- `04c-pods-watch.png`: `kubectl get pods -w`, the new Flask pods
- `05-hpa-peak.png`: 5 Flask pods and their CPU
- `06-hpa-scale-down.png`: back to 2 replicas
- `07-persistence.png`: data still there after deleting the Mongo pod
- `08-auth.png`: no password is rejected, with password works
- `09-dns.png`: `mongodb-service` resolves to its ClusterIP
- `10-hpa-events.png`: the scale up and scale down events

### Problems I ran into

1. Right after deploy the HPA showed `<unknown>` for CPU. metrics-server needed about a minute before it returned numbers. It fixed itself.
2. MongoDB went into CrashLoopBackOff the first time. I had an older test setup of my own still running on the same Minikube node (namespace `faralpha`). Its MongoDB pod used the same hostPath `/data/mongodb`, and its Service already had NodePort 30080, so my `flask-service` was rejected. I deleted that namespace and its PV, cleared `/data/mongodb` on the node, and recreated the pod. Also good to know: `MONGO_INITDB_ROOT_*` only works when the data folder is empty.
3. `docker push` said "denied" because I used my Linux username in the image name instead of my Docker Hub username. I retagged it and pushed again.
4. At first I was not sure how much load was needed. The pods must go over 140m each (70% of 200m), and 4 load generators were enough.

## Clean up

```bash
kubectl delete -f k8s/
minikube delete
```
