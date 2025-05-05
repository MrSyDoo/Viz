import random
import asyncio
import os
from pyrogram import idle
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant

loop = asyncio.get_event_loop()

# Bot API Information from environment variables
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = list(map(int, os.getenv("ADMINS", "").split(" ")))

PORT = "8080"
GWAY = False
DATABASE_URI = os.getenv("DATABASE_URI")
my_client = MongoClient(DATABASE_URI)
mydb = my_client["cluster0"]
participants = mydb["participants"]
broadcast = mydb["broadcast"]
fsub = mydb["fsub"]
cached_count = None

app = Client("giveaway_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# --- MongoDB ---
async def add_user(user_id):
    try:
        participants.insert_one({'_id': user_id})
        return True
    except DuplicateKeyError:
        return False

async def get_user_count():
    return participants.count_documents({})

async def delete_user_data():
    participants.delete_many({})

async def delete_user(user_id):
    result = participants.delete_one({'_id': user_id})
    return result.deleted_count > 0

async def get_broadcast_channel():
    doc = broadcast.find_one()
    return doc["_id"] if doc else None
    
async def add_broadcast_channel(channel_id: int):
    broadcast.delete_many({})
    broadcast.insert_one({"_id": channel_id})


async def add_fsub_channel(channel_id):
    try:
        fsub.insert_one({"_id": channel_id})
        return True
    except DuplicateKeyError:
        return False
        
async def remove_fsub_channel(channel_id):
    result = fsub.delete_one({"_id": channel_id})
    return result.deleted_count > 0

async def get_fsub_channels():
    return [doc["_id"] for doc in fsub.find()]

async def is_user_in_channels(bot, user_id):
    try:
        channels = await get_fsub_channels()
        for channel_id in channels:
            member = await bot.get_chat_member(channel_id, user_id)
            if member.status == ChatMemberStatus.BANNED:
                return False
    except UserNotParticipant:
        return False
    except Exception as e:
        print(f"Error checking user membership: {e}")
        return False
    return True


# --- Command Handlers ---
@app.on_message(filters.command("start"))
async def start(client, message: Message):
    await message.reply_text("Welcome to the Giveaway Bot!")

@app.on_message(filters.command("giveaway") & filters.user(ADMINS))
async def giveaway(client, message):
    global GWAY
    b_id = await get_broadcast_channel()
    if not b_id:
        await message.reply("No broadcast channel set.")
        return

    channels = [doc["_id"] for doc in fsub.find()]
    if not channels:
        await message.reply("No Fsub channels set.")
        return

    count = await get_user_count()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Join Giveaway", callback_data="join_giveaway")],
        [InlineKeyboardButton(f"Participants: {count}", callback_data="count_participants")]
    ])
    text = "Please Join On The Following Channels To Participate In The Giveaway ☺️:\n\n"
    for ch in channels:
        text += f"• @{ch}\n"
    text += "\n<i>Then Click On Join Giveaway</i>"
    try:
        sent = await client.send_message(
            chat_id=b_id,
            text=text,
            reply_markup=keyboard
        )
        # Store message info for updates
    except Exception as e:
        await message.reply_text(f"Error sending giveaway message:\n`{e}`", quote=True)
    GWAY = True
    await asyncio.sleep(60)
    global cached_count
    while True:
        current_count = await get_user_count()

        # Only update the message if the count has changed
        if current_count != cached_count:
            cached_count = current_count  
            kyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Join Giveaway", callback_data="join_giveaway")],
                [InlineKeyboardButton(f"Participants: {current_count}", callback_data="count_participants")]
           ])
            try:
                await client.edit_message_reply_markup(
                    chat_id=b_id,
                    message_id=sent.id,
                    reply_markup=kyboard
                )
            except Exception as e:
                print(f"Error updating giveaway message: {e}")

        await asyncio.sleep(8)
        
@app.on_callback_query(filters.regex("join_giveaway"))
async def join_giveaway_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    is_in_both_channels = await is_user_in_channels(client, user_id)

    if not is_in_both_channels:
        await callback_query.answer(
            text=f"Please Join On The Following Channels To Participate ☺️",
            show_alert=True
        )
    else:
        added = await add_user(user_id)
        if not added:
            await callback_query.answer("You have already joined!", show_alert=True)
        else:
            await callback_query.answer("You're in the giveaway! [Added] ", show_alert=True)

@app.on_callback_query(filters.regex("count_participants"))
async def count_partpants(client, callback_query: CallbackQuery):
    global GWAY
    count = await get_user_count()
    if GWAY:
        await callback_query.answer(f"Current Participants {count} !", show_alert=True)
    else: 
        await callback_query.answer("GIVEAWAY ENDED!", show_alert=True)
    

@app.on_message(filters.command("end") & filters.user(ADMINS))
async def end_giveaway(client, message):
    global GWAY
    try:
        number_to_pick = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.reply_text("Usage: /end <number>. Example: /end 5")
        return

    b_id = await get_broadcast_channel()
    users = participants.find()
    participant_ids = [str(user['_id']) for user in users]
    total_users = len(participant_ids)

    valid_ids = []
    for user_id in participant_ids:
        try:
            in_giveaway = await is_user_in_channels(client, int(user_id))
           # in_required = await is_user_in_channels(client, int(user_id), REQUIRED_CHANNEL_ID, REQUIRED_CHANNEL_USERNAME)

            if in_giveaway: #and in_required:
                valid_ids.append(user_id)
            else:
                await delete_user(int(user_id))  # Remove from DB
        except Exception as e:
            print(f"Error checking user {user_id}: {e}")
            continue

    valid_count = len(valid_ids)

    if number_to_pick > valid_count:
        await message.reply_text(f"Not enough valid participants (have: {valid_count}).")
        return

    random.shuffle(valid_ids)
    selected_ids = random.sample(valid_ids, number_to_pick)

    winner_text = []
    for user_id in selected_ids:
        try:
            user = await app.get_users(int(user_id))
            username = f"@{user.username}" if user.username else "No Username"
            winner_text.append(f"User ID: {user_id}, Username: {username}")
        except Exception:
            winner_text.append(f"User ID: {user_id}, Username: Unknown")

    await client.send_message(
        b_id,
        f"Total Participants: {total_users}\n"
        f"Valid Participants: {valid_count}\n\n"
        f"Selected Winners:\n" + "\n".join(winner_text)
    )
    GWAY = False

@app.on_message(filters.command("delbc") & filters.user(ADMINS))
async def clear_broadcast(client, message):
    result = broadcast.delete_many({})
    await message.reply_text(f"Cleared {result.deleted_count} broadcast channel(s).")

@app.on_message(filters.command("bc") & filters.user(ADMINS))
async def end_giveaway(client, message):
    try:
        channel_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.reply_text("Usage: /bc <id>. Example: /id -100xxxxxx5")
        return
    added = await add_broadcast_channel(channel_id)
    if added:
        await message.reply_text("Channel added to broadcast list.\nDon't Forget To Make Me Admin")
    else:
        await message.reply_text("Channel already exists. Use /delbc To Delete")

@app.on_message(filters.command("addfsub") & filters.user(ADMINS))
async def add_fsub(client, message):
    if len(message.command) < 2:
        return await message.reply("Usage: /addfsub <channel_id or @username>")
    
    channel = message.command[1]
    try:
        await client.get_chat(channel)
        fsub.insert_one({"_id": channel})
        await message.reply(f"Channel `{channel}` added to fsub list.\nDon't Forget To Make Me Admin")
    except Exception as e:
        await message.reply(f"Failed to add: {e}")


@app.on_message(filters.command("delfsub") & filters.user(ADMINS))
async def del_fsub(client, message):
    if len(message.command) < 2:
        return await message.reply("Usage: /delfsub <username> WithOut @")
    
    channel = message.command[1]
    result = fsub.delete_one({"_id": channel})
    if result.deleted_count:
        await message.reply(f"Channel `{channel}` removed from fsub list.")
    else:
        await message.reply("Channel not found in fsub list.")

@app.on_message(filters.command("setfsub") & filters.user(ADMINS))
async def view_fsub(client, message):
    channels = [doc["_id"] for doc in fsub.find()]
    if not channels:
        await message.reply("No fsub channels set.\nUse /addfsub To Add And /delfsub To Remove.")
        return

    text = "**Current FSub Channels:**\nUse /addfsub To Add And /delfsub To Remove.\n"
    for ch in channels:
        text += f"• `{ch}`\n Tᴏ Rᴇᴍᴏᴠᴇ `/delfsub {ch}`\n\n"

    await message.reply(text)


@app.on_message(filters.command("clear") & filters.user(ADMINS))
async def vclear(client, message):
    await delete_user_data()
    await message.reply("✅")
# --- Web Server ---
async def web_handler(request):
    return web.Response(text="Giveaway bot running.")

async def web_server():
    app_web = web.Application()
    app_web.add_routes([web.get("/", web_handler)])
    return app_web

#------------------------

# --- Start the Bot ---
async def main():
    print("Starting bot...")
    await app.start()
    runner = web.AppRunner(await web_server())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await idle()  # Keeps the bot running until manually stopped
    await app.stop()
    print("Bot stopped.")
#----------------------

if __name__ == "__main__":
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print('Service Stopped Bye 👋')
