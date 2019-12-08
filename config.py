import os

def update_config():
    token = os.environ['EMOJIBOT_TOKEN']
    bucket_endpoint = os.environ['EMOJIBOT_BUCKETENDPOINT']
    bucket_access_key = os.environ['EMOJIBOT_BUCKETACCESSKEY']
    bucket_secret_key = os.environ['EMOJIBOT_BUCKETSECRETKEY']
    bucket_name = os.environ['EMOJIBOT_BUCKETNAME']
    prefix = os.environ['EMOJIBOT_PREFIX']
    guild_id = int(os.environ['EMOJIBOT_GUILDID'])

    return {
        'token': token,
        'bucket_endpoint': bucket_endpoint,
        'bucket_access_key': bucket_access_key,
        'bucket_secret_key': bucket_secret_key,
        'bucket_name': bucket_name,
        'prefix': prefix,
        'guild_id': guild_id,
    }
