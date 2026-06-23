"""
DAG : FraudGuard Daily Fraud Report
Génère un rapport quotidien de fraude pour chaque type d'alerte.
Utilise le Dynamic Task Mapping pour paralléliser l'analyse par type.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator
from airflow.providers.google.cloud.operators.kubernetes_engine import GKEStartPodOperator

ALERT_TYPES = [
    'MICRO_TRANSACTION_PATTERN',
    'HIGH_VELOCITY',
    'SUSPICIOUS_IP',
]

with DAG(
    dag_id='fraudguard_daily_report',
    description='Rapport quotidien de fraude FraudGuard — MIF II & ACPR',
    default_args={
        'retries': 2,
        'retry_delay': timedelta(minutes=5),
        'email_on_failure': True,
        'email': ['fraud-ops@fraudguard.fr'],
    },
    start_date=datetime(2026, 1, 1),
    schedule='0 7 * * *',   # 07h00 chaque matin
    catchup=False,
    tags=['fraud', 'reporting', 'acpr'],
) as dag:

    # ============================================================
    # Tâche 1 : Récupérer la liste des types d'alertes depuis Firestore
    # (En prod : requête Firestore pour les types actifs du jour)
    # ============================================================
    def get_alert_types_for_date(**context):
        """Retourne la liste des types d'alertes à analyser pour hier."""
        execution_date = context['ds']
        print(f"[FraudGuard] Récupération des types d'alertes pour {execution_date}")
        # En prod : requête Firestore/BigQuery pour obtenir les types actifs
        return ALERT_TYPES

    fetch_alert_types = PythonOperator(
        task_id='fetch_alert_types',
        python_callable=get_alert_types_for_date,
    )

    # ============================================================
    # Tâche 2 : Analyser chaque type d'alerte EN PARALLÈLE
    # Dynamic Task Mapping : une tâche par type d'alerte
    # ============================================================
    def analyze_alert_type(alert_type: str, **context):
        """Analyser les statistiques d'un type d'alerte spécifique."""
        execution_date = context['ds']
        print(f"[FraudGuard] Analyse {alert_type} pour {execution_date}")

        # Simulation de l'analyse (en prod : requête BigQuery)
        import random
        stats = {
            'alert_type': alert_type,
            'date': execution_date,
            'count': random.randint(5, 200),
            'unique_accounts_affected': random.randint(2, 50),
            'total_amount_at_risk': round(random.uniform(100, 50000), 2),
            'avg_detection_latency_ms': random.randint(50, 500),
            'false_positive_rate': round(random.uniform(0.02, 0.15), 3),
        }

        print(f"[FraudGuard] {alert_type}: {stats['count']} alertes, "
              f"{stats['unique_accounts_affected']} comptes, "
              f"{stats['total_amount_at_risk']}€ à risque")
        return stats

    # Dynamic Task Mapping : .expand() génère N tâches en parallèle
    analyze_by_type = PythonOperator.partial(
        task_id='analyze_alert_type',
        python_callable=analyze_alert_type,
    ).expand(
        op_kwargs=[{'alert_type': t} for t in ALERT_TYPES]
    )

    # ============================================================
    # Tâche 3 : Consolider les analyses de tous les types
    # ============================================================
    def consolidate_report(**context):
        """Agréger les analyses de tous les types d'alertes en un rapport global."""
        # Récupérer les résultats de toutes les tâches expand
        all_stats = context['ti'].xcom_pull(task_ids='analyze_alert_type')

        if not all_stats:
            print("[FraudGuard] Aucune donnée à consolider")
            return

        total_alerts = sum(s['count'] for s in all_stats)
        total_amount = sum(s['total_amount_at_risk'] for s in all_stats)

        report = {
            'date': context['ds'],
            'total_alerts': total_alerts,
            'total_amount_at_risk': round(total_amount, 2),
            'breakdown_by_type': all_stats,
            'generated_at': datetime.now().isoformat(),
            'regulatory_compliant': True,
        }

        print(f"\n{'='*50}")
        print(f"RAPPORT FRAUDE FraudGuard — {context['ds']}")
        print(f"Total alertes : {total_alerts}")
        print(f"Montant à risque : {total_amount:.2f}€")
        print(f"{'='*50}\n")

        context['ti'].xcom_push(key='daily_report', value=report)
        return report

    consolidate = PythonOperator(
        task_id='consolidate_report',
        python_callable=consolidate_report,
    )

    # ============================================================
    # Tâche 4 : Charger le rapport dans BigQuery
    # ============================================================
    load_report_bq = BigQueryInsertJobOperator(
        task_id='load_report_to_bigquery',
        configuration={
            'query': {
                'query': """
                    INSERT INTO `fraudguard-prod.reporting.daily_fraud_summary`
                    VALUES (
                        '{{ ds }}',
                        {{ ti.xcom_pull(task_ids='consolidate_report', key='daily_report')['total_alerts'] }},
                        {{ ti.xcom_pull(task_ids='consolidate_report', key='daily_report')['total_amount_at_risk'] }},
                        CURRENT_TIMESTAMP()
                    )
                """,
                'useLegacySql': False,
            }
        },
        gcp_conn_id='google_cloud_default',
    )

    # ============================================================
    # Tâche 5 : Réentraîner le modèle ML si le taux de faux positifs est trop élevé
    # KubernetesPodOperator : lance un pod dédié avec ses propres ressources
    # ============================================================
    def check_should_retrain(**context):
        """Vérifier si les faux positifs justifient un réentraînement du modèle."""
        all_stats = context['ti'].xcom_pull(task_ids='analyze_alert_type')
        avg_fp_rate = sum(s['false_positive_rate'] for s in all_stats) / len(all_stats)
        print(f"[FraudGuard] Taux de faux positifs moyen : {avg_fp_rate:.2%}")
        should_retrain = avg_fp_rate > 0.10
        context['ti'].xcom_push(key='should_retrain', value=should_retrain)
        return should_retrain

    check_retrain = PythonOperator(
        task_id='check_should_retrain',
        python_callable=check_should_retrain,
    )

    # Lance un pod Kubernetes avec GPU pour le réentraînement du modèle
    retrain_model = GKEStartPodOperator(
        task_id='retrain_fraud_model',
        project_id='{{ var.value.gcp_project_id }}',
        location='europe-west9',
        cluster_name='fraudguard-cluster',
        namespace='fraudguard',
        image='europe-west9-docker.pkg.dev/PROJECT_ID/tp2-registry/fraud-ml-trainer:latest',
        name='fraud-model-retrain-{{ ds_nodash }}',
        arguments=[
            '--training-date', '{{ ds }}',
            '--model-output', 'gs://fraudguard-models/fraud-detector-{{ ds_nodash }}',
        ],
        resources={
            'request_memory': '4Gi',
            'request_cpu': '2',
            'limit_memory': '8Gi',
        },
        get_logs=True,
    )

    # ============================================================
    # Tâche 6 : Détecter un jour anormalement frauduleux
    # TriggerDagRunOperator : déclencher le DAG d'investigation
    # ============================================================
    def check_anomalous_day(**context):
        """Détecter si ce jour est anormalement frauduleux."""
        report = context['ti'].xcom_pull(task_ids='consolidate_report', key='daily_report')
        # Seuil : > 500 alertes en une journée = anormal
        is_anomalous = report['total_alerts'] > 500
        context['ti'].xcom_push(key='is_anomalous', value=is_anomalous)
        if is_anomalous:
            print(f"[ALERTE] Jour anormal détecté : {report['total_alerts']} alertes !")
        return is_anomalous

    check_anomaly = PythonOperator(
        task_id='check_anomalous_day',
        python_callable=check_anomalous_day,
    )

    # Déclencher le DAG d'investigation si la journée est anormale
    trigger_investigation = TriggerDagRunOperator(
        task_id='trigger_investigation',
        trigger_dag_id='fraudguard_deep_investigation',
        conf={
            'triggered_by': 'daily_report',
            'trigger_date': '{{ ds }}',
            'alert_count': "{{ ti.xcom_pull(task_ids='consolidate_report', key='daily_report')['total_alerts'] }}",
        },
        wait_for_completion=False,   # Ne pas bloquer le rapport en attendant l'investigation
    )

    # ============================================================
    # Ordre des tâches
    # ============================================================
    fetch_alert_types >> analyze_by_type >> consolidate >> [load_report_bq, check_retrain, check_anomaly]
    check_retrain >> retrain_model
    check_anomaly >> trigger_investigation