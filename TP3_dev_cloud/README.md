                         ┌─────────────────────┐
                         │  Camions LogiStream │
                         │  (GPS embarqué IoT) │
                         └──────────┬──────────┘
                                    │
                          Envoi positions GPS
                                    │
                                    ▼
                     ┌─────────────────────────┐
                     │      GPS Producer       │
                     │  (simulation Node.js)   │
                     │   déployé sur GKE       │
                     └──────────┬──────────────┘
                                │
                      Publish événements Kafka
                                │
                                ▼
                  ┌────────────────────────────┐
                  │       Apache Kafka         │
                  │  Topic : truck-positions   │
                  │  (6 partitions, replicated)│
                  └──────────┬─────────────────┘
                             │
                  Consumer Group KafkaJS
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌────────────────┐ ┌────────────────┐ ┌────────────────┐
│ tracker-cons.1 │ │ tracker-cons.2 │ │ tracker-cons.3 │
│   partition 0  │ │   partition 2  │ │   partition 4  │
│   partition 1  │ │   partition 3  │ │   partition 5  │
└────────┬───────┘ └────────┬───────┘ └────────┬───────┘
         │                  │                  │
         └──────────────────┴──────────────────┘
                            │
                 Traitement des positions
                            │
                            ▼
                 ┌────────────────────┐
                 │   Tracker Service  │
                 │ API / Agrégation   │
                 └─────────┬──────────┘
                           │
                    API REST / WebSocket
                           │
                           ▼
                 ┌────────────────────┐
                 │ Dashboard Logistique│
                 │  Suivi temps réel   │
                 │   des camions       │
                 └────────────────────┘


Infrastructure :
─────────────────────────────────────────────────────────
- Kubernetes GKE (Google Kubernetes Engine)
- HPA pour autoscaling des consumers
- LoadBalancer GCP pour exposition API
- Artifact Registry pour images Docker
- Monitoring & Alerting via Google Cloud Monitoring