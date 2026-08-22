import { EmbedBuilder } from 'discord.js';

const COLORS = {
  BUY: 0x2ecc71, // vert
  SELL: 0xe74c3c, // rouge
};

function fmt(value) {
  return value === undefined ? 'N/A' : String(value);
}

export function buildSignalEmbed(signal) {
  const embed = new EmbedBuilder()
    .setTitle(`${signal.action === 'BUY' ? '🟢 ACHAT' : '🔴 VENTE'} — ${signal.symbol}`)
    .setColor(COLORS[signal.action])
    .addFields(
      { name: 'Prix d’entrée', value: fmt(signal.price), inline: true },
      { name: 'Stop Loss', value: fmt(signal.stopLoss), inline: true },
      { name: 'Take Profit', value: fmt(signal.takeProfit), inline: true },
      { name: 'Timeframe', value: fmt(signal.timeframe), inline: true },
      { name: 'Stratégie', value: fmt(signal.strategy), inline: true },
    )
    .setTimestamp(new Date(signal.receivedAt))
    .setFooter({ text: 'Signal informatif — aucune exécution automatique. Décision de trade libre.' });

  return embed;
}
