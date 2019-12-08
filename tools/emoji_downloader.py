import argparse
import discord
import requests
import os
import sys

class MyClient(discord.Client):
    async def on_ready(self):
        print('Logged in as')
        print(self.user.name)
        print(self.user.id)
        print('------')

        guild = self.get_guild(args.guild_id)
        for i in guild.emojis:
            emojiUrl = i.url

            res = requests.get(emojiUrl)
            res.raise_for_status()

            imageFile = open(os.path.join(args.output_dir, os.path.basename(i.name)), 'wb')
            for chunk in res.iter_content(100000):
                imageFile.write(chunk)
            imageFile.close()

        sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("guild_id", help="ID of the guild that owns the desired emojis", type=int)
    parser.add_argument("token", help="Bot Token to access Discord API", type=str)
    parser.add_argument("output_dir", help="Directory to store the downloaded emojis", type=str)
    args = parser.parse_args()

    client = MyClient()
    client.run(args.token)
