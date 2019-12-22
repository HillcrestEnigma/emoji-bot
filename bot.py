import discord
import re
import config
from minio import Minio
from minio.error import ResponseError
import datetime
import random
import time
import io
import asyncio

emoji_regex = re.compile(r':(.*?):')
config_dict = config.update_config()
minioClient = Minio(config_dict['bucket_endpoint'],
                  access_key=config_dict['bucket_access_key'],
                  secret_key=config_dict['bucket_secret_key'],
                  secure=True)
help_message = [
    ['listemojis', 'List all emojis available.'],
    ['emoji [Emoji Name]', 'Display information on the emoji specified.'],
    ['help', 'Display this glorious help message.']
]
status = {"maintain_emoji_state": "idle"}
emoji_id_dict = {}

class MyClient(discord.Client):
    async def get_guild_emoji_state(self, guild_id):
        guild = client.get_guild(guild_id)
        guild_emojis = guild.emojis
        guild_emoji_names = [i.name for i in guild_emojis]
        duplicates = []
        for i in guild_emoji_names:
            if guild_emoji_names.count(i) > 1 and not i in duplicates:
                duplicates.append(i)
        for i in duplicates:
            emoji = discord.utils.get(guild_emojis, name=i)
            await emoji.delete()
        return set(guild_emoji_names)

    async def set_guild_emoji_state(self, guild_id, bucket_name, new_state):
        guild = client.get_guild(guild_id)
        guild_state = await self.get_guild_emoji_state(guild_id)
        to_delete = guild_state - new_state
        to_add = new_state - guild_state
        for i in to_delete:
            await discord.utils.get(guild.emojis, name=i).delete()
        for i in to_add:
            emoji_data = minioClient.get_object(bucket_name, i).read()
            emoji = await guild.create_custom_emoji(name=i, image=emoji_data)
            emoji_id_dict[emoji.name] = emoji.id

    async def get_bucket_emoji_state(self, bucket_name):
        return set([i.object_name for i in minioClient.list_objects_v2(bucket_name)])

    async def set_bucket_emoji_state(self, guild_id, bucket_name, new_state):
        guild = client.get_guild(guild_id)
        bucket_state = await self.get_bucket_emoji_state(bucket_name)
        to_delete = bucket_state - new_state
        to_add = new_state - bucket_state
        minioClient.remove_objects(bucket_name, to_delete)
        to_add_emoji_objects = [i for i in guild.emojis if i.name in to_add]
        for i in to_add_emoji_objects:
            emoji_data = io.BytesIO(await i.url.read())
            emoji_data.seek(0, 2)
            emoji_data_len = emoji_data.tell()
            emoji_data.seek(0, 0)
            minioClient.put_object(bucket_name, i.name, emoji_data, length=emoji_data_len, content_type="image/png")


    async def maintain_emoji_state(self, guild_id, bucket_name):
        if (not status['maintain_emoji_state'] == "idle") and time.time() - status['maintain_emoji_state'] < 300:
            return

        status['maintain_emoji_state'] = time.time()

        guild = client.get_guild(guild_id)
        guild_state = await self.get_guild_emoji_state(guild_id)
        bucket_state = await self.get_bucket_emoji_state(bucket_name)

        if guild_state == bucket_state:
            return
        if not guild_state.issubset(bucket_state):
            await self.set_bucket_emoji_state(guild_id, bucket_name, bucket_state.union(guild_state))
        if len(guild_state) < min(len(bucket_state), guild.emoji_limit - 1):
            num_needed_guild_emojis = min(len(bucket_state), guild.emoji_limit - 1) - len(guild_state)
            list_bucket_unique_state = list(bucket_state - guild_state)
            random.shuffle(list_bucket_unique_state)
            await self.set_guild_emoji_state(guild_id, bucket_name, guild_state.union(set(list_bucket_unique_state[:num_needed_guild_emojis])))
        if len(guild_state) == guild.emoji_limit:
            guild_emojis = list(guild.emojis)
            guild_emojis.sort(key=lambda x: x.created_at)
            await self.set_guild_emoji_state(guild_id, bucket_name, guild_state - {guild_emojis[0].name})

        status['maintain_emoji_state'] = "idle"

    def sub_emoji(self, matchObj):
        emoji_id = emoji_id_dict[matchObj.group(1)]
        return "<:{0}:{1}>".format(matchObj.group(1), emoji_id)

    async def delete_emoji(self, guild_id, bucket_name, emoji_name, edit_guild=True):
        if edit_guild:
            guild = client.get_guild(guild_id)
            emoji = await discord.utils.get(guild.emojis, name=curName)
            await emoji.delete()
        minioClient.remove_object(bucket_name, curName)

    async def rename_emoji(self, guild_id, bucket_name, curName, newName, edit_guild=True):
        if edit_guild:
            guild = client.get_guild(guild_id)
            emoji = await discord.utils.get(guild.emojis, name=curName)
            await emoji.edit(name="newName")
        curObj = minioClient.stat_object(bucket_name, curName)
        minioClient.copy_object(bucket_name, newName, curName, metadata=curObj.metadata)
        self.delete_emoji(guild_id, bucket_name, curName, edit_guild=False)

    async def on_ready(self):
        print('Logged in as')
        print(self.user.name)
        print(self.user.id)

        self.guild = await client.fetch_guild(config_dict['guild_id'])
        for i in self.guild.emojis:
            emoji_id_dict[i.name] = i.id
        await self.maintain_emoji_state(config_dict['guild_id'], config_dict['bucket_name'])

    async def on_guild_emojis_update(self, guild, before, after):
        global status
        if guild.id == config_dict['guild_id']:
            await self.maintain_emoji_state(config_dict['guild_id'], config_dict['bucket_name'])

    async def on_message(self, message):
        global config_dict
        if message.guild.id == config_dict['guild_id']:
            # we do not want the bot to reply to itself
            if message.author.id == self.user.id or message.webhook_id:
                return

            if message.content.startswith(config_dict['prefix']):
                if message.content == config_dict['prefix'] + 'listemojis':
                    objects = minioClient.list_objects_v2(config_dict['bucket_name'])
                    embed = discord.Embed(title="Emojis of {0}".format(message.guild.name))
                    emoji_textlist = []
                    for i in objects:
                        if (not None == i.metadata) and 'x-amz-meta-info' in i.metadata:
                            info = i.metadata['x-amz-meta-info']
                        else:
                            info = '*Last Modified at {0}*'.format(i.last_modified)
                            info = "*No info provided*"
                        # emoji_textlist.append("{0} - {1}".format(i.object_name, info))
                        emoji_textlist.append(i.object_name)
                    embed.description = "\n".join(emoji_textlist)
                    await message.channel.send(embed=embed)
                elif message.content.startswith(config_dict['prefix'] + 'emoji'):
                    emoji_name = message.content.split(" ")[1]
                    emoji = minioClient.stat_object(config_dict['bucket_name'], emoji_name)
                    emoji_url = minioClient.presigned_get_object(config_dict['bucket_name'], emoji_name, expires=datetime.timedelta(days=2))
                    if (not None == emoji.metadata) and 'x-amz-meta-info' in emoji.metadata:
                        info = emoji.metadata['x-amz-meta-info']
                    else:
                        info = '*Not available*'

                    embed = discord.Embed(title=':{0}:'.format(emoji_name))
                    embed.url = emoji_url
                    embed.set_thumbnail(url=emoji_url)
                    embed.add_field(name="Info", value=info)
                    elmt = emoji.last_modified
                    last_modified_text = "{0}-{1}-{2} {3}:{4}:{5}".format(elmt.tm_year, elmt.tm_mon, elmt.tm_mday, elmt.tm_hour, elmt.tm_min, elmt.tm_sec)
                    embed.add_field(name="Last modified", value=last_modified_text)

                    await message.channel.send(embed=embed)
                elif message.content == config_dict['prefix'] + "help":
                    embed = discord.Embed(title="Help")
                    for i in help_message:
                        embed.add_field(name=config_dict['prefix'] + i[0], value=i[1])
                    await message.channel.send(embed=embed)
                elif message.content == config_dict['prefix'] + "reload":
                    config_dict = config.update_config()
                elif message.content == config_dict['prefix'] + "maintainstate":
                    await self.maintain_emoji_state(config_dict['guild_id'], config_dict['bucket_name'])
            else:
                emoji_matches = set([i for i in emoji_regex.findall(message.content)])
                guild_emojis = set([i.name for i in await message.guild.fetch_emojis()])
                absent_emojis = emoji_matches - guild_emojis
                if len(absent_emojis) > 0:
                    present_emojis = emoji_matches.union(guild_emojis)
                    other_emojis = guild_emojis - present_emojis
                    other_emojis_list = list(other_emojis)
                    random.shuffle(other_emojis_list)
                    set_emoji_coro = asyncio.create_task(self.set_guild_emoji_state(config_dict['guild_id'], config_dict['bucket_name'], absent_emojis.union(present_emojis).union(set(other_emojis_list[:len(other_emojis_list) - len(absent_emojis)]))))
                    maintain_emoji_coro = asyncio.create_task(self.maintain_emoji_state(config_dict['guild_id'], config_dict['bucket_name']))
                    done, pending = await asyncio.wait([set_emoji_coro, maintain_emoji_coro], return_when=asyncio.ALL_COMPLETED)
                    if not set_emoji_coro in done:
                        return
                    new_message = re.sub(emoji_regex, self.sub_emoji, message.content)
                    webhook = await message.channel.create_webhook(name=message.author.display_name, avatar=await message.author.avatar_url.read())
                    await webhook.send(new_message)
                    await webhook.delete()
                    await message.delete()


client = MyClient()
client.run(config_dict['token'])
