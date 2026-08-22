import express from 'express';
import { config } from './config.js';
import { validateSignal } from './validate.js';
import { saveSignal } from './store.js';

export function createServer(onSignal) {
  const app = express();
  app.use(express.json());

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok' });
  });

  // Webhook appelé par les alertes TradingView
  app.post('/webhook/tradingview', (req, res) => {
    const secret = req.headers['x-webhook-secret'] || req.body?.secret;
    if (secret !== config.webhookSecret) {
      return res.status(401).json({ error: 'Secret invalide.' });
    }

    const { valid, signal, error } = validateSignal(req.body);
    if (!valid) {
      return res.status(400).json({ error });
    }

    saveSignal(signal);

    if (onSignal) {
      onSignal(signal).catch((err) => console.error('[discord] Publication échouée :', err.message));
    }

    res.status(202).json({ received: true, id: signal.id });
  });

  return app;
}
