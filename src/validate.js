const ACTIONS = ['BUY', 'SELL'];

/**
 * Valide le payload envoyé par le webhook TradingView.
 * Retourne { valid: true, signal } ou { valid: false, error }.
 */
export function validateSignal(payload) {
  if (!payload || typeof payload !== 'object') {
    return { valid: false, error: 'Payload JSON invalide.' };
  }

  for (const field of ['symbol', 'action']) {
    if (!payload[field] || typeof payload[field] !== 'string') {
      return { valid: false, error: `Champ requis manquant ou invalide : ${field}.` };
    }
  }

  const action = payload.action.toUpperCase();
  if (!ACTIONS.includes(action)) {
    return { valid: false, error: `Action inconnue : ${payload.action} (attendu BUY ou SELL).` };
  }

  const num = (v) => (v === undefined || v === null || v === '' ? undefined : Number(v));
  const price = num(payload.price);
  const stopLoss = num(payload.stopLoss);
  const takeProfit = num(payload.takeProfit);

  for (const [name, value] of [['price', price], ['stopLoss', stopLoss], ['takeProfit', takeProfit]]) {
    if (value !== undefined && Number.isNaN(value)) {
      return { valid: false, error: `Champ ${name} doit être numérique.` };
    }
  }

  return {
    valid: true,
    signal: {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      symbol: payload.symbol.toUpperCase(),
      action,
      price,
      stopLoss,
      takeProfit,
      timeframe: payload.timeframe || 'N/A',
      strategy: payload.strategy || 'N/A',
      receivedAt: new Date().toISOString(),
    },
  };
}
