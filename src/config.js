import 'dotenv/config';

function required(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Variable d'environnement manquante : ${name}. Copie .env.example en .env et remplis les valeurs.`);
  }
  return value;
}

export const config = {
  discordToken: required('DISCORD_TOKEN'),
  channelId: required('DISCORD_CHANNEL_ID'),
  port: Number(process.env.PORT) || 3000,
  webhookSecret: required('WEBHOOK_SECRET'),
};
