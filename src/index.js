import { config } from './config.js';
import { createServer } from './server.js';
import { startDiscord } from './discord.js';

async function main() {
  const discord = await startDiscord();

  const app = createServer((signal) => discord.publishSignal(signal));
  app.listen(config.port, () => {
    console.log(`[webhook] Serveur en écoute sur http://localhost:${config.port}/webhook/tradingview`);
  });

  const shutdown = () => {
    console.log('\nArrêt du bot...');
    discord.shutdown();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

main().catch((err) => {
  console.error('Erreur au démarrage :', err.message);
  process.exit(1);
});
