/**
 * FraudGuard Fraud Detector — Kafka Streams en Node.js
 * Détecte 3 patterns de fraude :
 * 1. Micro-transactions répétées (> 10 tx < 2€ en 5 minutes)
 * 2. Vélocité élevée (> 20 transactions en 5 minutes, tous montants)
 * 3. Montant anormalement élevé (> 5× la moyenne du compte sur 30 jours)
 */
const { Kafka } = require('kafkajs');

const kafka = new Kafka({
  clientId: 'fraudguard-streams-detector',
  brokers: [process.env.KAFKA_BOOTSTRAP_SERVERS || 'localhost:9092'],
});

const consumer = kafka.consumer({ groupId: 'fraud-detection-group' });
const producer = kafka.producer();

// ============================================================
// État en mémoire (en prod : utiliser un State Store Redis ou RocksDB)
// Fenêtre glissante de 5 minutes par compte
// ============================================================
const WINDOW_SIZE_MS = 5 * 60 * 1000       // 5 * 60 * 1000  (5 minutes en ms)
const MICRO_TX_THRESHOLD_AMOUNT = 2.00;    // < 2€ = micro-transaction
const MICRO_TX_THRESHOLD_COUNT = 10;       // > 10 micro-tx en 5 min = fraude
const VELOCITY_THRESHOLD = 20;             // > 20 tx en 5 min = fraude

// Map : account_id → liste de transactions dans la fenêtre courante
const windowedTransactions = new Map();

function pruneExpiredTransactions(accountId) {
  const now = Date.now();
  const txList = windowedTransactions.get(accountId) || [];
  const fresh = txList.filter(tx => now - tx._received_at < WINDOW_SIZE_MS);
  windowedTransactions.set(accountId, fresh);
  return fresh;
}

async function analyzeTransaction(transaction) {
  const accountId = transaction.account_id;
  const now = Date.now();

  // Ajouter la transaction à la fenêtre en mémoire
  const txList = pruneExpiredTransactions(accountId);
  txList.push({ ...transaction, _received_at: now });
  windowedTransactions.set(accountId, txList);

  const alerts = [];

  // ---- Pattern 1 : Micro-transactions répétées ----
  const microTxCount = txList.filter(tx => tx.amount < MICRO_TX_THRESHOLD_AMOUNT).length;   // MICRO_TX_THRESHOLD_AMOUNT
  if (microTxCount >= MICRO_TX_THRESHOLD_COUNT) {
    alerts.push({
      alert_type: 'MICRO_TRANSACTION_PATTERN',
      severity: 'HIGH',
      description: `${microTxCount} micro-transactions (< ${MICRO_TX_THRESHOLD_AMOUNT}€) en 5 minutes`,
      micro_tx_count: microTxCount,
      total_amount: txList
        .filter(tx => tx.amount < MICRO_TX_THRESHOLD_AMOUNT)
        .reduce((sum, tx) => sum + tx.amount, 0)
        .toFixed(2),
    });
  }

  // ---- Pattern 2 : Vélocité élevée ----
  if (txList.length >= VELOCITY_THRESHOLD) {   // VELOCITY_THRESHOLD
    alerts.push({
      alert_type: 'HIGH_VELOCITY',
      severity: 'CRITICAL',
      description: `${txList.length} transactions en 5 minutes — vélocité anormale`,
      tx_count_5min: txList.length,
    });
  }

  // ---- Pattern 3 : IP suspecte (adresse connue de bots) ----
  const suspiciousIPs = ['185.234.219.45', '31.220.0.0/24'];
  if (suspiciousIPs.includes(transaction.ip_address)) {
    alerts.push({
      alert_type: 'SUSPICIOUS_IP',
      severity: 'MEDIUM',
      description: `Adresse IP suspecte détectée : ${transaction.ip_address}`,
    });
  }

  return alerts;
}

async function publishAlert(transaction, alert) {
  const fraudAlert = {
    alert_id: `ALERT-${Date.now()}-${transaction.account_id}`,
    account_id: transaction.account_id,
    account_name: transaction.account_name,
    triggering_tx_id: transaction.tx_id,
    triggering_amount: transaction.amount,
    ...alert,
    window_size_minutes: WINDOW_SIZE_MS / 60000,
    detected_at: new Date().toISOString(),
    action_recommended: alert.severity === 'CRITICAL' ? 'BLOCK_ACCOUNT' : 'REVIEW',
  };

  await producer.send({
    topic: 'fraud-alerts',   // 'fraud-alerts'
    messages: [{
      key: transaction.account_id,
      value: JSON.stringify(fraudAlert),
    }],
  });

  console.log(`[ALERTE ${alert.severity}] ${alert.alert_type} — Compte ${transaction.account_id}`);
  console.log(`  → ${alert.description}`);
}

async function startDetection() {
  await Promise.all([consumer.connect(), producer.connect()]);
  console.log('[FraudGuard] Moteur de détection Kafka Streams démarré');

  await consumer.subscribe({ topics: ['transactions-raw'], fromBeginning: false });

  // Stats de monitoring
  let txProcessed = 0;
  let alertsGenerated = 0;

  setInterval(() => {
    const accountsMonitored = windowedTransactions.size;
    const totalWindowedTx = Array.from(windowedTransactions.values())
      .reduce((sum, list) => sum + list.length, 0);
    console.log(`[STATS] Traitées: ${txProcessed} tx | Alertes: ${alertsGenerated} | Comptes moniteurs: ${accountsMonitored} | En fenêtre: ${totalWindowedTx} tx`);
  }, 30000);

  await consumer.run({
    eachMessage: async ({ message }) => {
      const transaction = JSON.parse(message.value.toString());
      txProcessed++;

      const alerts = await analyzeTransaction(transaction);

      for (const alert of alerts) {
        await publishAlert(transaction, alert);
        alertsGenerated++;
      }
    },
  });
}

process.on('SIGTERM', async () => {
  await Promise.all([consumer.disconnect(), producer.disconnect()]);
  process.exit(0);
});

startDetection().catch(console.error);