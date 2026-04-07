# TP2 Dev Cloud - Architecture et commandes

Ce fichier resume l'architecture et les commandes vues tout au long du TP2 (Docker avance, Cloud Run et networking GCP).

## 1. Architecture globale du TP2

Le TP2 suit un flux en 3 etapes :

1. Developpement et test en local avec Docker Compose.
2. Build et publication de l'image dans Artifact Registry.
3. Deploiement serverless sur Cloud Run, puis exercices reseau et stockage avances sur GCP.

## 2. Architecture applicative

### 2.1 Application

Service principal Node.js + TypeScript (Express) dans [tp2-app/src/index.ts](tp2-app/src/index.ts).

Endpoints :
- / : reponse JSON de base
- /health : verification sante (etat DB)
- /db : ecrit/compte des visites dans PostgreSQL
- /cached : lecture avec cache Redis (TTL)

### 2.2 Conteneurisation

- Dockerfile naive : [tp2-app/Dockerfile.naive](tp2-app/Dockerfile.naive)
- Dockerfile multi-stage (build + runtime) : [tp2-app/Dockerfile](tp2-app/Dockerfile)

Le multi-stage permet de compiler dans un stage de build puis de garder une image de runtime plus legere et plus sure.

### 2.3 Orchestration locale (Docker Compose)

Fichier : [tp2-app/docker-compose.yml](tp2-app/docker-compose.yml)

Services :
- web (Node.js)
- db (PostgreSQL)
- cache (Redis)

Composants techniques :
- reseau dedie : app-network
- volume persistant : postgres-data
- depends_on avec condition service_healthy

Schema logique local :

Client -> web -> PostgreSQL
Client -> web -> Redis (cache)

### 2.4 Architecture cloud

- Artifact Registry : stockage de l'image Docker versionnee
- Cloud Run : execution serverless de l'image
- Cloud Run revisions : deploiement progressif avec traffic splitting
- VPC custom + subnets + firewall : segmentation et controle reseau
- Cloud Storage versioning/lifecycle : protection des objets et optimisation des couts

## 3. Commandes vues pendant le TP2

## 3.1 Build Docker (naif vs multi-stage)

```bash
cd tp2-app

docker build -f Dockerfile.naive -t tp2-naive:v1 .
docker images tp2-naive:v

docker build -t tp2-app:v1 .
docker images | grep tp
```

Build compatible Cloud Run depuis Mac ARM :

```bash
docker buildx build --platform linux/amd64 -t "${IMAGE}" .
```

## 3.2 Dependances Node.js

```bash
npm install pg
npm install --save-dev @types/pg
npm install redis
```

## 3.3 Docker Compose (app + postgres + redis)

```bash
docker-compose up -d --build
docker-compose ps
docker-compose logs -f
docker-compose stop
docker-compose down -v
```

Tests locaux :

```bash
curl http://localhost:8080/
curl http://localhost:8080/health
curl http://localhost:8080/db
curl http://localhost:8080/cached
```

## 3.4 Artifact Registry

Creation du repository :

```bash
gcloud artifacts repositories create tp2-registry \
  --repository-format=docker \
  --location=europe-west9 \
  --description="Registry TP2 YNOV"

gcloud artifacts repositories list
```

Authentification Docker :

```bash
gcloud auth configure-docker europe-west9-docker.pkg.dev
cat ~/.docker/config.json | grep -A3 "credHelpers"
```

Tag + push :

```bash
PROJECT_ID=$(gcloud config get-value project)
IMAGE_TAG="europe-west9-docker.pkg.dev/${PROJECT_ID}/tp2-registry/tp2-app:v1"

docker tag tp2-app:v1 ${IMAGE_TAG}
docker push ${IMAGE_TAG}

gcloud artifacts docker images list \
  europe-west9-docker.pkg.dev/${PROJECT_ID}/tp2-registry
```

## 3.5 Deploiement Cloud Run

```bash
PROJECT_ID=$(gcloud config get-value project)
IMAGE="europe-west9-docker.pkg.dev/${PROJECT_ID}/tp2-registry/tp2-app:v1"

gcloud run deploy tp2-service \
  --image=${IMAGE} \
  --region=europe-west9 \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --memory=512Mi \
  --cpu=1 \
  --max-instances=3 \
  --set-env-vars="APP_ENV=production"
```

Verification :

```bash
SERVICE_URL=$(gcloud run services describe tp2-service \
  --region=europe-west9 \
  --format='value(status.url)')

echo "URL du service : ${SERVICE_URL}"
curl ${SERVICE_URL}
curl ${SERVICE_URL}/health

gcloud run services describe tp2-service --region=europe-west9
gcloud run services list --region=europe-west9
```

## 3.6 Revisions Cloud Run et traffic splitting

Nouvelle revision sans trafic :

```bash
gcloud run deploy tp2-service \
  --image=${IMAGE} \
  --region=europe-west9 \
  --no-traffic \
  --set-env-vars="APP_ENV=production,APP_VERSION=2.0.0" \
  --tag=v02

gcloud run revisions list --service=tp2-service --region=europe-west9
```

Repartition 80/20 :

```bash
REV_STABLE=$(gcloud run revisions list \
  --service=tp2-service --region=europe-west9 \
  --format="value(name)" | sed -n '2p')

REV_CANARY=$(gcloud run revisions list \
  --service=tp2-service --region=europe-west9 \
  --format="value(name)" | sed -n '1p')

gcloud run services update-traffic tp2-service \
  --region=europe-west9 \
  --to-revisions="${REV_STABLE}=80,${REV_CANARY}=20"

gcloud run services describe tp2-service \
  --region=europe-west9 \
  --format="yaml(status.traffic)"
```

Promotion 100% latest :

```bash
gcloud run services update-traffic tp2-service \
  --region=europe-west9 \
  --to-latest
```

## 3.7 Reseau GCP (VPC, subnets, firewall)

Creation VPC et sous-reseaux :

```bash
gcloud compute networks create tp2-vpc --subnet-mode=custom

gcloud compute networks subnets create tp2-subnet-public \
  --network=tp2-vpc \
  --region=europe-west9 \
  --range=10.10.1.0/24

gcloud compute networks subnets create tp2-subnet-private \
  --network=tp2-vpc \
  --region=europe-west9 \
  --range=10.10.2.0/24
```

Firewall :

```bash
gcloud compute firewall-rules create tp2-allow-http \
  --network=tp2-vpc \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:80 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=http-server

gcloud compute firewall-rules create tp2-allow-https \
  --network=tp2-vpc \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:443 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=http-server

gcloud compute firewall-rules create tp2-allow-postgres \
  --network=tp2-vpc \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:5432 \
  --source-ranges=10.10.1.0/24 \
  --target-tags=db-server

gcloud compute firewall-rules list --filter="network=tp2-vpc"
```

Nettoyage :

```bash
gcloud compute firewall-rules delete tp2-allow-http --quiet
gcloud compute firewall-rules delete tp2-allow-https --quiet
gcloud compute firewall-rules delete tp2-allow-postgres --quiet

gcloud compute networks subnets delete tp2-subnet-public --region=europe-west9 --quiet
gcloud compute networks subnets delete tp2-subnet-private --region=europe-west9 --quiet
gcloud compute networks delete tp2-vpc --quiet
```

## 3.8 Cloud Storage avance (versioning + lifecycle)

```bash
PROJECT_ID=$(gcloud config get-value project)
BUCKET="ynov-tp2-versioned-${PROJECT_ID}"

gcloud storage buckets create gs://${BUCKET} \
  --location=europe-west9 \
  --uniform-bucket-level-access

gcloud storage buckets update gs://${BUCKET} --versioning
gcloud storage buckets describe gs://${BUCKET} --format="value(versioning.enabled)"

echo "Version 1 - $(date)" > config.json
gcloud storage cp config.json gs://${BUCKET}/

echo "Version 2 - $(date)" > config.json
gcloud storage cp config.json gs://${BUCKET}/

echo "Version 3 - $(date)" > config.json
gcloud storage cp config.json gs://${BUCKET}/

gcloud storage ls -a gs://${BUCKET}/config.json
```

Application lifecycle :

```bash
gcloud storage buckets update gs://${BUCKET} --lifecycle-file=lifecycle.json
gcloud storage buckets describe gs://${BUCKET} --format="json(lifecycle)"
```

Nettoyage final versions :

```bash
gcloud storage rm -r --all-versions gs://${BUCKET}
```

## 4. Resume express

- Local : app Node.js conteneurisee, orchestree avec PostgreSQL + Redis via Docker Compose.
- Livraison image : Artifact Registry.
- Execution cloud : Cloud Run avec revisions et traffic splitting.
- Infrastructure : VPC custom, subnets, firewall rules.
- Data : bucket GCS avec versioning et lifecycle pour optimisation cout/risque.
