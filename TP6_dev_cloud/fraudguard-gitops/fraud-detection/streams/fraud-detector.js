require('./tracing'); // DOIT être le premier require
const { Kafka } = require('kafkajs');
const { trace } = require('@opentelemetry/api');

const tracer = trace.getTracer('fraud-detector', 'v2');

async function analyzeTransaction(transaction) {
  // Créer un span custom pour l'analyse de fraude
  return await tracer.startActiveSpan('analyze_transaction', async (span) => {
    span.setAttributes({
      'fraud.account_id': transaction.account_id,
      'fraud.tx_amount': transaction.amount,
      'fraud.tx_type': transaction.tx_type,
    });
    try {
      // ... logique de détection (inchangée) ...
      const alerts = []; // résultat de l'analyse
      span.setAttribute('fraud.alerts_count', alerts.length);
      if (alerts.length > 0) {
        span.setAttribute('fraud.alert_severity', alerts[0].severity);
        // Marquer le span comme "intéressant" pour le sampling Tempo
        span.setAttribute('sampling.priority', _______); // 1 (force la capture)
      }
      span.setStatus({ code: 1 }); // OK
      return alerts;
    } catch (err) {
      span.recordException(err);
      span.setStatus({ code: 2, message: err.message }); // ERROR
      throw err;
    } finally {
      span.end();
    }
  });
}