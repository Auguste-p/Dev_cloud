# FraudGuard — Détection de Fraude en Temps Réel

Système de détection de fraude pour la néobanque FraudGuard (groupe Meridian), traitant 3 millions de transactions/jour avec détection en temps réel via Kafka Streams et reporting réglementaire via Airflow.

---

## Architecture Complète

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        NAMESPACE: fraudguard (GKE)                       │
│                                                                           │
│  ┌──────────────┐     ┌─────────────────────────────────────────────┐   │
│  │  tx-producer │────▶│           Kafka (Strimzi 3.7)               │   │
│  │  (1 replica) │     │  ┌─────────────────┐  ┌──────────────────┐ │   │
│  │              │     │  │ transactions-raw │  │   fraud-alerts   │ │   │
│  │ 20 tx/s      │     │  │  6 partitions   │  │  3 partitions    │ │   │
│  │ légitimes    │     │  │  rétention 24h  │  │  rétention 30j   │ │   │
│  │ +            │     │  └────────┬────────┘  └────────▲─────────┘ │   │
│  │ 25 micro-tx  │     │           │                    │            │   │
│  │ /60s (fraude)│     └───────────┼────────────────────┼────────────┘   │
│  └──────────────┘                 │                    │                 │
│                                   ▼                    │                 │
│                        ┌──────────────────┐            │                 │
│                        │  fraud-detector  │────────────┘                 │
│                        │  (1 replica)     │                              │
│                        │                  │  Détecte 3 patterns :        │
│                        │  Fenêtre 5 min   │  • Micro-tx (>10 de <2€)    │
│                        │  en mémoire      │  • Vélocité (>20 tx/5min)   │
│                        │  (windowedTx Map)│  • IP suspecte              │
│                        └──────────────────┘                              │
│                                                                           │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                    alert-handler (2 replicas)                      │   │
│  │  Consomme fraud-alerts → Firestore (audit) + actions automatiques  │   │
│  │  CRITICAL → BLOCK_ACCOUNT | HIGH → LIMIT(50€) | MEDIUM → LOG      │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
          │                                          │
          ▼                                          ▼
┌──────────────────┐                    ┌──────────────────────────┐
│    Firestore     │                    │   Namespace: monitoring   │
│  fraud_alerts    │                    │   Prometheus + Grafana    │
│  blocked_accounts│                    │   Alertmanager            │
└──────────────────┘                    │   PodMonitor → Kafka JMX  │
                                        └──────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Namespace: airflow (Helm)                          │
│                                                                       │
│  DAG: fraudguard_daily_report (07h00 quotidien)                      │
│                                                                       │
│  fetch_alert_types                                                    │
│        │                                                             │
│        ▼                                                             │
│  analyze_alert_type[0]  analyze_alert_type[1]  analyze_alert_type[2] │
│  (MICRO_TX)             (HIGH_VELOCITY)         (SUSPICIOUS_IP)      │
│  ◄── Dynamic Task Mapping (.expand()) ──►                            │
│        │                                                             │
│        ▼                                                             │
│  consolidate_report                                                   │
│        │                                                             │
│   ┌────┴────────────────┐                                            │
│   ▼                     ▼                      ▼                    │
│  load_to_bigquery   check_retrain         check_anomaly              │
│                         │                      │                    │
│                         ▼                      ▼                    │
│                  retrain_fraud_model   trigger_investigation         │
│                  (KubernetesPodOp)     (TriggerDagRunOp)            │
│                  GPU pod 8Gi RAM        ──▶ fraudguard_deep_         │
│                                             investigation DAG        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Réponses aux Questions

### Question 1.3 — État distribué du fraud-detector

**Problème :**
Avec 3 réplicas du `fraud-detector`, la Map `windowedTransactions` est locale à chaque pod. Deux problèmes critiques :

1. **État fragmenté** : les 25 micro-transactions de `ACC-9999` sont distribuées entre les 3 pods selon la partition Kafka assignée. Chaque pod ne voit qu'un tiers des transactions, donc le seuil de 10 micro-transactions n'est jamais atteint sur un seul pod → **la fraude n'est pas détectée**.

2. **Perte d'état au redémarrage** : si un pod crash ou est re-schedulé par Kubernetes, toute la fenêtre en mémoire est perdue. Un compte frauduleux peut recommencer une attaque juste après un redémarrage sans être détecté pendant 5 minutes.

**Solution recommandée — Redis avec partitionnement :**
Utiliser **Redis** (ou Redis Cluster) comme State Store partagé. Chaque pod écrit et lit la fenêtre depuis Redis avec une clé `window:{account_id}` et un TTL de 5 minutes (EXPIRE). Le partitionnement Kafka garantit que les transactions du même `account_id` arrivent sur le même pod, donc un seul pod lit/écrit l'état Redis d'un compte donné — pas de conflit de concurrence. En prod, Kafka Streams Java utilise **RocksDB** comme State Store local avec replication changelog pour la tolérance aux pannes.

---

### Question 2.1 — KubernetesPodOperator vs PythonOperator pour le réentraînement ML

**PythonOperator standard :**
- S'exécute dans le worker Airflow existant, partage ses ressources (RAM, CPU)
- Un réentraînement consommant 8 Go de RAM + 2 GPU **tuerait le worker Airflow** ou serait refusé par le scheduler
- Impossible d'attacher des ressources GPU spécifiques au worker
- Pas d'isolation : une OOM du training crash le worker et impacte tous les autres DAGs

**KubernetesPodOperator :**
- Lance un pod Kubernetes **éphémère et isolé** avec ses propres resource requests/limits
- Peut demander `nvidia.com/gpu: 2` dans les resources → scheduled sur un node GPU dédié
- Le worker Airflow reste léger (il surveille juste le pod, n'exécute pas le training)
- Si le pod crash, seule la tâche `retrain_fraud_model` échoue, pas tout le DAG
- Image Docker dédiée au training avec ses propres dépendances (TensorFlow, PyTorch, etc.) sans polluer l'environnement Airflow

**Conclusion :** Pour tout workload intensif en ressources (ML training, Spark jobs, ETL lourd), le `KubernetesPodOperator` est la seule approche viable. Le `PythonOperator` est réservé aux tâches légères d'orchestration et de décision.

---

### Question 3.4 — Garantir une latence < 2s avec consumer lag élevé

**Problème :** Avec un lag de 5000 messages et un débit de 20 tx/s, le fraud-detector a ~4 minutes de retard. Les 25 micro-transactions de l'attaque ne sont analysées que 4 minutes après leur émission.

**Architecture recommandée — Double pipeline :**

```
transactions-raw
       │
       ├──▶ [fraud-detector NORMAL]   ← consomme en régime normal, lag acceptable
       │     (fenêtre 5 min, détection complète)
       │
       └──▶ [fast-path detector]      ← consomme en PRIORITÉ HAUTE
              (règles simples : IP suspecte, montant > seuil absolu)
              Latence < 200ms garantie
              Pas d'état, stateless → scalable horizontalement
```

**Mécanismes concrets :**
1. **Fast-path stateless** : un second consumer group `fast-fraud-detection-group` ne fait que des règles sans état (IP blocklist, montant > 10 000€, device fingerprint connu). Latence < 200ms garantie car pas de gestion de fenêtre.
2. **Auto-scaling du fraud-detector** : HPA basé sur `kafka_consumergroup_lag` via KEDA. Si lag > 500, scale de 1 → 4 réplicas. Chaque réplica prend 1-2 partitions → le lag se résorbe en < 60s.
3. **Séparation des partitions** : dédier les partitions 0-2 au fast-path (toutes les transactions) et partitions 3-5 au fraud-detector complet. Le producer envoie les comptes à risque connus sur les partitions fast-path.

---

## Tableau d'observations Kafka Streams (à remplir pendant le TP)

| Métrique | Valeur observée | Seuil FraudGuard | Status |
|---|---|---|---|
| Transactions/s en régime normal | 35 | < 50 | OK |
| Transactions/s pendant l'attaque | 250 | Détecté si > 200 | Détecté |
| Consumer lag fraud-detector | 5000 | < 100 | CRITIQUE |
| Latence détection P99 | 1500 ms | < 500ms | CRITIQUE |
| Alertes générées en 10 min | 2 | — | — |

---

## Prérequis et déploiement

### 1. Cluster GKE + Strimzi
```bash
# Créer le namespace
kubectl create namespace fraudguard

# Déployer Strimzi operator (si pas déjà fait)
kubectl apply -f https://strimzi.io/install/latest?namespace=kafka -n kafka
kubectl wait --for=condition=Ready pod -l name=strimzi-cluster-operator -n kafka --timeout=120s

# Déployer Kafka FraudGuard
kubectl apply -f kafka/fraudguard-cluster.yaml
kubectl wait kafka/fraudguard-kafka --for=condition=Ready --timeout=300s -n fraudguard
```

### 2. Builder et pusher les images
```bash
PROJECT_ID=$(gcloud config get-value project)

# Remplacer PROJECT_ID dans le fichier de déploiement
sed -i "s/PROJECT_ID/${PROJECT_ID}/g" k8s/fraudguard-deployments.yaml

for service in producer streams alert-service; do
  docker build \
    -t europe-west9-docker.pkg.dev/${PROJECT_ID}/tp2-registry/fraudguard-${service}:v1 \
    fraud-detection/${service}/
  docker push europe-west9-docker.pkg.dev/${PROJECT_ID}/tp2-registry/fraudguard-${service}:v1
done
```

### 3. Déployer les services
```bash
kubectl apply -f k8s/fraudguard-deployments.yaml
```

### 4. Stack monitoring
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.adminPassword="FraudGuard2026!" \
  --set grafana.service.type=LoadBalancer \
  --timeout 10m
```

### 5. Airflow
```bash
helm repo add apache-airflow https://airflow.apache.org
helm install airflow apache-airflow/airflow \
  --namespace airflow --create-namespace \
  --set dags.persistence.enabled=true \
  --timeout 10m
```