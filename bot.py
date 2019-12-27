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
used_emoji_regex = re.compile(r'<a?:(\w+):\d+>')
config_dict = config.update_config()
minioClient = Minio(config_dict['bucket_endpoint'],
                  access_key=config_dict['bucket_access_key'],
                  secret_key=config_dict['bucket_secret_key'],
                  secure=True)
help_message = [
    ['l [Page Number]', 'List all emojis available.'],
    ['s [Query] [Page Number]', 'Searches for emojis'],
    ['emoji [Emoji Name]', 'Display information on the emoji specified.'],
    ['help', 'Display this glorious help message.']
]
status = {"maintain_emoji_state": "idle"}
emoji_dict = {}

class Paginator:
    def __init__(self, items, per_page=10):
        self.items = items
        self.per_page = per_page
    
    def num_pages(self, per_page=None):
        if per_page == None:
            per_page = self.per_page
        return len(self.items) // per_page + 1

    def get_page(self, page, per_page=None):
        if per_page == None:
            per_page = self.per_page
        num_pages = self.num_pages(per_page)
        if page >= 0 and page <= num_pages-1:
            if page == num_pages-1:
                return self.items[per_page*page:]
            else:
                return self.items[per_page*page:per_page*(page+1)]
        else:
            raise IndexError

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

        result = {'regular': set(), 'animated': set()}
        guild = client.get_guild(guild_id)
        guild_emojis = guild.emojis
        for i in guild_emojis:
            if i.animated:
                result['animated'].add(i.name)
            else:
                result['regular'].add(i.name)
        return result

    async def set_guild_emoji_state(self, guild_id, bucket_name, new_state):
        guild = client.get_guild(guild_id)
        guild_state = await self.get_guild_emoji_state(guild_id)

        if type(new_state) == type(set()):
            bucket_state = await self.get_bucket_emoji_state(bucket_name)
            new_state_dict = {'animated': set(), 'regular': set()}
            for i in new_state:
                if i in bucket_state['regular']:
                    new_state_dict['regular'].add(i)
                elif i in new_state_dict['animated']:
                    new_state_dict['animated'].add(i)
            new_state = new_state_dict

        if "regular" in new_state:
            to_delete_regular = (guild_state['regular'] - new_state['regular'])
            to_add_regular = (new_state['regular'] - guild_state['regular'])
            for i in to_delete_regular:
                await discord.utils.get(guild.emojis, name=i).delete()
            for i in to_add_regular:
                emoji_data = minioClient.get_object(bucket_name, "regular/" + i).read()
                emoji = await guild.create_custom_emoji(name=i, image=emoji_data)
                emoji_dict[emoji.name] = [emoji.id, emoji.animated]

        if "animated" in new_state:
            to_delete_animated = (guild_state['animated'] - new_state['animated'])
            to_add_animated = (new_state['animated'] - guild_state['animated'])
            for i in to_delete_animated:
                await discord.utils.get(guild.emojis, name=i).delete()
            for i in to_add_animated:
                emoji_data = minioClient.get_object(bucket_name, "animated/" + i).read()
                emoji = await guild.create_custom_emoji(name=i, image=emoji_data)
                emoji_dict[emoji.name] = [emoji.id, emoji.animated]

    async def get_bucket_emoji_state(self, bucket_name):
        result = {}
        result['regular'] = set([i.object_name.replace("regular/", "") for i in minioClient.list_objects_v2(bucket_name, prefix="regular/") if not i.object_name == 'regular/'])
        result['animated'] = set([i.object_name.replace("animated/", "") for i in minioClient.list_objects_v2(bucket_name, prefix="animated/") if not i.object_name == 'animated/'])
        return result

    async def set_bucket_emoji_state(self, guild_id, bucket_name, new_state):
        guild = client.get_guild(guild_id)
        bucket_state = await self.get_bucket_emoji_state(bucket_name)
        to_add_emoji_objects = []

        if "regular" in new_state:
            to_delete_regular = bucket_state['regular'] - new_state['regular']
            to_add_regular = new_state['regular'] - bucket_state['regular']
            minioClient.remove_objects(bucket_name, ["regular/" + i for i in to_delete_regular])

            to_add_emoji_objects.extend([i for i in guild.emojis if i.name in to_add_regular])
        if "animated" in new_state:
            to_delete_animated = bucket_state['animated'] - new_state['animated']
            to_add_animated = new_state['animated'] - bucket_state['animated']
            minioClient.remove_objects(bucket_name, ['animated/' + i for i in to_delete_animated])
            to_add_emoji_objects.extend([i for i in guild.emojis if i.name in to_add_animated])

        for i in to_add_emoji_objects:
            emoji_data = io.BytesIO(await i.url.read())
            emoji_data.seek(0, 2)
            emoji_data_len = emoji_data.tell()
            emoji_data.seek(0, 0)
            if i.animated:
                minioClient.put_object(bucket_name, "animated/" + i.name, emoji_data, length=emoji_data_len, content_type="image/gif")
            else:
                minioClient.put_object(bucket_name, "regular/" + i.name, emoji_data, length=emoji_data_len, content_type="image/png")


    async def maintain_emoji_state(self, guild_id, bucket_name):
        if (not status['maintain_emoji_state'] == "idle") and time.time() - status['maintain_emoji_state'] < 300:
            return

        status['maintain_emoji_state'] = time.time()

        guild = client.get_guild(guild_id)
        total_guild_state = await self.get_guild_emoji_state(guild_id)
        total_bucket_state = await self.get_bucket_emoji_state(bucket_name)

        for emoji_type in ['regular', 'animated']:
            guild_state = total_guild_state[emoji_type]
            bucket_state = total_bucket_state[emoji_type]

            if guild_state == bucket_state:
                continue
            if not guild_state.issubset(bucket_state):
                await self.set_bucket_emoji_state(guild_id, bucket_name, {emoji_type: bucket_state.union(guild_state)})
            if len(guild_state) < min(len(bucket_state), guild.emoji_limit - 1):
                num_needed_guild_emojis = min(len(bucket_state), guild.emoji_limit - 1) - len(guild_state)
                list_bucket_unique_state = list(bucket_state - guild_state)
                random.shuffle(list_bucket_unique_state)
                await self.set_guild_emoji_state(guild_id, bucket_name, {emoji_type: guild_state.union(set(list_bucket_unique_state[:num_needed_guild_emojis]))})
            if len(guild_state) == guild.emoji_limit:
                guild_emojis = [i for i in guild.emojis if i.name in guild_state]
                guild_emojis.sort(key=lambda x: x.created_at)
                await self.set_guild_emoji_state(guild_id, bucket_name, {emoji_type: guild_state - {guild_emojis[0].name}})

        status['maintain_emoji_state'] = "idle"

    def sub_used_emoji(self, matchObj):
        return ":{0}:".format(matchObj.group(1))

    def sub_emoji(self, matchObj):
        emoji = emoji_dict[matchObj.group(1)]
        emoji_id = emoji[0]
        emoji_animated = emoji[1]
        if emoji_animated:
            return "<a:{0}:{1}>".format(matchObj.group(1), emoji_id)
        else:
            return "<:{0}:{1}>".format(matchObj.group(1), emoji_id)

    async def delete_emoji(self, guild_id, bucket_name, emoji_name, edit_guild=True):
        guild = client.get_guild(guild_id)
        if edit_guild:
            emoji = discord.utils.get(guild.emojis, name=curName)
            await emoji.delete()
        if emoji_dict[emoji_name][1]:
            obj_prefix = "animated/"
        else:
            obj_prefix = "regular/"
        minioClient.remove_object(bucket_name, obj_prefix + emoji_name)

    async def rename_emoji(self, guild_id, bucket_name, curName, newName, edit_guild=True):
        guild = client.get_guild(guild_id)
        if edit_guild:
            emoji = discord.utils.get(guild.emojis, name=curName)
            await emoji.edit(name="newName")
        else:
            emoji = discord.utils.get(guild.emojis, name=newName)
        if emoji.animated:
            obj_prefix = "animated/"
        else:
            obj_prefix = "regular/"
        curObj = minioClient.stat_object(bucket_name, obj_prefix + curName)
        metadata = curObj.metadata
        minioClient.copy_object(bucket_name, obj_prefix + newName, '{0}/{1}'.format(bucket_name, obj_prefix + curName), metadata=metadata)
        await self.delete_emoji(guild_id, bucket_name, curName, edit_guild=False)

    async def on_ready(self):
        print('Logged in as')
        print(self.user.name)
        print(self.user.id)

        self.guild = await client.fetch_guild(config_dict['guild_id'])
        for i in self.guild.emojis:
            emoji_dict[i.name] = [i.id, i.animated]
        await self.maintain_emoji_state(config_dict['guild_id'], config_dict['bucket_name'])

    async def on_guild_emojis_update(self, guild, before, after):
        if guild.id == config_dict['guild_id']:

            guild_state = await self.get_guild_emoji_state(guild.id)
            all_emojis = guild_state['regular'].union(guild_state['animated'])
            for i in all_emojis:
                if i not in emoji_dict:
                    emoji = discord.utils.get(guild.emojis, name=i)
                    emoji_dict[i] = [emoji.id, emoji.animated]

            emoji_names_before = [i.name for i in before]
            emoji_names_after = [i.name for i in after]

            if len(before) == len(after):
                old_name = list(set(emoji_names_before) - set(emoji_names_after))[0]
                new_name = list(set(emoji_names_after) - set(emoji_names_before))[0]
                await self.rename_emoji(config_dict['guild_id'], config_dict['bucket_name'], old_name, new_name, False)
            elif len(before) > len(after):
                deleted_emoji = list(set(emoji_names_before) - set(emoji_names_after))[0]
                audit_log = await guild.audit_logs(limit=5, action=discord.AuditLogAction.emoji_delete).flatten()
                action = discord.utils.find(lambda x: x.before.name == deleted_emoji, audit_log)
                if not action.user.id == self.user.id:
                    await self.delete_emoji(config_dict['guild_id'], config_dict['bucket_name'], deleted_emoji, False)

            await self.maintain_emoji_state(config_dict['guild_id'], config_dict['bucket_name'])

    async def on_message(self, message):
        global config_dict
        if message.guild.id == config_dict['guild_id']:
            # we do not want the bot to reply to itself
            if message.author.id == self.user.id or message.webhook_id:
                return

            if message.content.startswith(config_dict['prefix']):
                command = message.content[len(config_dict['prefix']):].split(" ")
                if command[0] in ['listemojis', 'listemotes', 'le', 'ls', 'list', 'l']:
                    if len(command) == 1:
                        page = 0
                    else:
                        page = int(command[1]) - 1
                    bucket_state = await self.get_bucket_emoji_state(config_dict['bucket_name'])
                    all_emojis = bucket_state['regular'].union(bucket_state['animated'])
                    all_emojis_list = list(all_emojis)
                    all_emojis_list.sort()
                    paginator = Paginator(all_emojis_list, 50)
                    num_pages = paginator.num_pages()
                    emojis = paginator.get_page(page)
                    next_page = (page+2)%num_pages
                    if next_page == 0:
                        next_page = num_pages
                    guild_state = await self.get_guild_emoji_state(config_dict['guild_id'])
                    all_guild_emojis = guild_state['regular'].union(guild_state['animated'])
                    embed = discord.Embed(title="Emojis of {0}".format(message.guild.name))
                    emoji_textlist = ['**__Page {0}/{1}__**\n'.format(page+1, num_pages)]
                    for i in emojis:
                        obj_name = '{0}'.format(i)
                        if i in all_guild_emojis:
                            obj_name = '**{0}**'.format(obj_name)
                        if i in bucket_state['animated']:
                            obj_name = '*{0}*'.format(obj_name)
                        emoji_textlist.append(obj_name)
                    emoji_textlist.append('\n**__Page {0}/{1}__**'.format(page+1, num_pages))
                    emoji_textlist.append('*Type `{0}l {1}` to view the next page*'.format(config_dict['prefix'], next_page))
                    embed.description = "\n".join(emoji_textlist)
                    await message.channel.send(embed=embed)
                elif command[0] in ["search", "s"]:
                    query = command[1]
                    bucket_state = await self.get_bucket_emoji_state(config_dict['bucket_name'])
                    all_emojis = bucket_state['regular'].union(bucket_state['animated'])
                    matching_emojis = [i for i in all_emojis if query.lower() in i.lower()]
                    matching_emojis.sort()

                    if len(command) == 2:
                        page = 0
                    else:
                        page = int(command[2]) - 1
                    
                    paginator = Paginator(matching_emojis, 50)
                    num_pages = paginator.num_pages()
                    emojis = paginator.get_page(page)
                    next_page = (page+2)%num_pages
                    if next_page == 0:
                        next_page = num_pages
                    guild_state = await self.get_guild_emoji_state(config_dict['guild_id'])
                    all_guild_emojis = guild_state['regular'].union(guild_state['animated'])
                    embed = discord.Embed(title="Matching emojis for `{0}`".format(query))
                    emoji_textlist = ['**__Page {0}/{1}__**\n'.format(page+1, num_pages)]
                    for i in emojis:
                        obj_name = '{0}'.format(i)
                        if i in all_guild_emojis:
                            obj_name = '**{0}**'.format(obj_name)
                        if i in bucket_state['animated']:
                            obj_name = '*{0}*'.format(obj_name)
                        query_start_index = obj_name.lower().index(query.lower())
                        query_end_index = query_start_index + len(query)
                        obj_name = '{0}__{1}__{2}'.format(obj_name[:query_start_index], obj_name[query_start_index:query_end_index], obj_name[query_end_index:])
                        emoji_textlist.append(obj_name)
                    emoji_textlist.append('\n**__Page {0}/{1}__**'.format(page+1, num_pages))
                    emoji_textlist.append('*Type `{0}s {1} {2}` to view the next page*'.format(config_dict['prefix'], query, next_page))
                    embed.description = "\n".join(emoji_textlist)
                    await message.channel.send(embed=embed)

                elif message.content.startswith(config_dict['prefix'] + 'emoji'):
                    emoji_name = message.content.split(" ")[1]
                    bucket_state = await self.get_bucket_emoji_state(config_dict['bucket_name'])
                    if emoji_name in bucket_state['regular']:
                        emoji_type = 'regular'
                    else:
                        emoji_type = 'animated'
                    emoji = minioClient.stat_object(config_dict['bucket_name'], '{0}/{1}'.format(emoji_type, emoji_name))
                    emoji_url = minioClient.presigned_get_object(config_dict['bucket_name'], '{0}/{1}'.format(emoji_type, emoji_name), expires=datetime.timedelta(days=2))
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
                if len(emoji_matches) > 0:
                    guild_state = await self.get_guild_emoji_state(config_dict['guild_id'])
                    bucket_state = await self.get_bucket_emoji_state(config_dict['bucket_name'])

                    used_regular_emojis = set([i for i in emoji_matches if i in bucket_state['regular']])
                    absent_regular_emojis = used_regular_emojis - guild_state['regular']
                    present_regular_emojis = used_regular_emojis.union(guild_state['regular'])
                    other_regular_emojis = guild_state['regular'] - present_regular_emojis
                    other_regular_emojis_list = list(other_regular_emojis)
                    random.shuffle(other_regular_emojis_list)
                    desired_other_regular_emojis = set(other_regular_emojis_list[:len(other_regular_emojis) - len(absent_regular_emojis)])
                    desired_regular_emoji_state = absent_regular_emojis.union(present_regular_emojis).union(desired_other_regular_emojis)
                    
                    used_animated_emojis = set([i for i in emoji_matches if i in bucket_state['animated']])
                    absent_animated_emojis = used_animated_emojis - guild_state['animated']
                    present_animated_emojis = used_animated_emojis.union(guild_state['animated'])
                    other_animated_emojis = guild_state['animated'] - present_animated_emojis
                    other_animated_emojis_list = list(other_animated_emojis)
                    random.shuffle(other_animated_emojis_list)
                    desired_other_animated_emojis = set(other_animated_emojis_list[:len(other_animated_emojis) - len(absent_regular_emojis)])
                    desired_animated_emoji_state = absent_animated_emojis.union(present_animated_emojis).union(desired_other_animated_emojis)

                    total_desired_state = {'regular': desired_regular_emoji_state, 'animated': desired_animated_emoji_state}
                    await self.set_guild_emoji_state(config_dict['guild_id'], config_dict['bucket_name'], total_desired_state)

                    if len(absent_regular_emojis) > 0 or len(used_animated_emojis) > 0:
                        await self.maintain_emoji_state(config_dict['guild_id'], config_dict['bucket_name'])
                        print("====")
                        print(message.content)
                        new_message = re.sub(used_emoji_regex, self.sub_used_emoji, message.content)
                        print(new_message)
                        new_message = re.sub(emoji_regex, self.sub_emoji, new_message)
                        print(new_message)
                        webhook = await message.channel.create_webhook(name=message.author.display_name, avatar=await message.author.avatar_url.read())
                        await webhook.send(new_message)
                        await webhook.delete()
                        await message.delete()


client = MyClient()
client.run(config_dict['token'])
