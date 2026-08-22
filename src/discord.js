import { Client, GatewayIntentBits } from 'discord.js';
import { config } from './config.js';
import { buildSignalEmbed } from './embeds.js';

export async function startDiscord() {
  const client = new Client({ intents: [GatewayIntentBits.Guilds] });

  client.once('ready', () => {
    console.log(`[discord] Connecté en tant que ${client.user.tag}`);
  });

  await client.login(config.discordToken);

  const channel = async () => {
    const ch = await client.channels.fetch(config.channelId);
    if (!ch || !ch.isTextBased()) {
      throw new Error(`Salon Discord introuvable ou non textuel (ID: ${config.channelId}).`);
    }
    return ch;
  };

  return {
    publishSignal: async (signal) => {
      const ch = await channel();
      await ch.send({ content: `@here Nouveau signal **${signal.symbol}**`, embeds: [buildSignalEmbed(signal)] });
    },
    shutdown: () => client.destroy(),
  };
}
