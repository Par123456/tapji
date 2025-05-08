import telebot
import asyncio
import logging
import sqlite3
import random
import time
import json
import re
import schedule
from datetime import datetime, timedelta
from collections import defaultdict
from telethon import TelegramClient, events, sync, functions, types
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.types import InputPeerEmpty, Channel, User
from telethon.errors import FloodWaitError, ChannelPrivateError, UserBannedInChannelError

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    filename='bot_log.txt'
)
logger = logging.getLogger(__name__)
# Add console handler for debugging
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

# API Configuration
api_id = '20508268'
api_hash = '65d0d52b67f1e6d7256f22fb13864cd0'
bot_token = '7997960421:AAGvgLwhelNC4Xw9mhbdoKYGHWYud38z3Ic'
admin_ids = [5528371749]

# Create client connection
client = TelegramClient('advanced_session', api_id, api_hash)
bot = telebot.TeleBot(bot_token)

# Database connection
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

# Database setup
def setup_database():
    logger.info("Setting up database...")
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS groups
    (group_id INTEGER PRIMARY KEY, 
    name TEXT,
    join_date TEXT,
    member_count INTEGER,
    last_activity TEXT)
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages
    (message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT,
    message_text TEXT,
    priority INTEGER)
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS stats
    (stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER,
    keyword TEXT,
    response_count INTEGER,
    last_response TEXT)
    ''')

    conn.commit()
    logger.info("Database setup complete")

# Message templates
messages = {
    'kermanshah': [
        {'text': "پیام ویژه کرمانشاه 1", 'priority': 1},
        {'text': "پیام ویژه کرمانشاه 2", 'priority': 2},
    ],
    'mobile': [
        {'text': "پیام ویژه موبایل 1", 'priority': 1},
        {'text': "پیام ویژه موبایل 2", 'priority': 2},
    ],
    'phone': [
        {'text': "پیام ویژه گوشی 1", 'priority': 1},
        {'text': "پیام ویژه گوشی 2", 'priority': 2},
    ]
}

# Rate limits
rate_limits = {
    'group_join': 20,
    'message_send': 30,
    'keyword_cooldown': 300
}

class AdvancedBot:
    def __init__(self):
        self.joined_groups = set()
        self.message_queue = asyncio.Queue()
        self.last_responses = {}
        self.is_initialized = False
        
    async def initialize(self):
        """Initialize the bot with existing groups from database"""
        logger.info("Initializing bot...")
        self.load_joined_groups()
        self.is_initialized = True
        logger.info(f"Bot initialized with {len(self.joined_groups)} joined groups")
        
    def load_joined_groups(self):
        """Load the list of joined groups from database"""
        cursor.execute("SELECT group_id FROM groups")
        self.joined_groups = set(row[0] for row in cursor.fetchall())
        logger.info(f"Loaded {len(self.joined_groups)} groups from database")

    async def smart_join_groups(self):
        """Intelligently join groups based on keywords and rate limits"""
        logger.info("Starting smart group join process...")
        try:
            # Get all dialogs (conversations)
            result = await client(GetDialogsRequest(
                offset_date=None,
                offset_id=0,
                offset_peer=InputPeerEmpty(),
                limit=100,
                hash=0
            ))
            
            # Log the number of dialogs found
            logger.info(f"Found {len(result.dialogs)} dialogs")
            
            # Track how many groups we join in this run
            joined_count = 0
            
            for dialog in result.dialogs:
                if dialog.is_channel or dialog.is_group:
                    entity = dialog.entity
                    
                    # Skip if already joined
                    if entity.id in self.joined_groups:
                        continue
                        
                    # Check if the group name contains our target keywords
                    if hasattr(entity, 'title'):
                        group_name = entity.title.lower()
                        logger.debug(f"Checking group: {group_name}")
                        
                        if any(keyword in group_name for keyword in ['کرمانشاه', 'موبایل', 'گوشی']):
                            if await self.can_join_group():
                                logger.info(f"Attempting to join group: {group_name}")
                                await self.join_and_initialize_group(entity)
                                joined_count += 1
                                
                                # Delay between joins to avoid rate limits
                                await asyncio.sleep(random.uniform(30, 60))
                                
                                # Stop after joining a few groups to avoid rate limits
                                if joined_count >= 5:
                                    logger.info("Reached join limit for this run")
                                    break
            
            logger.info(f"Smart join process completed. Joined {joined_count} new groups.")
            
        except Exception as e:
            logger.error(f"Error in smart_join_groups: {str(e)}", exc_info=True)

    async def can_join_group(self):
        """Check if we can join more groups based on rate limits"""
        try:
            hour_ago = datetime.now() - timedelta(hours=1)
            cursor.execute("SELECT COUNT(*) FROM groups WHERE join_date > ?", 
                          (hour_ago.strftime('%Y-%m-%d %H:%M:%S'),))
            recent_joins = cursor.fetchone()[0]
            can_join = recent_joins < rate_limits['group_join']
            logger.debug(f"Recent joins: {recent_joins}, Can join: {can_join}")
            return can_join
        except Exception as e:
            logger.error(f"Error in can_join_group: {str(e)}", exc_info=True)
            return False

    async def join_and_initialize_group(self, group):
        """Join a group and initialize it in the database"""
        try:
            # Try to join the channel/group
            await client(JoinChannelRequest(group))
            
            # Get participant count if available
            try:
                full_channel = await client(functions.channels.GetFullChannelRequest(group))
                participant_count = full_channel.full_chat.participants_count
            except Exception:
                participant_count = 0
                
            # Add to our joined groups set
            self.joined_groups.add(group.id)
            
            # Add to database
            cursor.execute("""
                INSERT INTO groups (group_id, name, join_date, member_count, last_activity)
                VALUES (?, ?, ?, ?, ?)
            """, (group.id, group.title, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                  participant_count, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()

            logger.info(f"Successfully joined and initialized group: {group.title} (ID: {group.id})")
            
        except Exception as e:
            logger.error(f"Error joining group {group.title if hasattr(group, 'title') else 'Unknown'}: {str(e)}", exc_info=True)

    async def smart_message_handler(self, event):
        """Process incoming messages for keywords"""
        try:
            # Check if this is a group message
            if not event.is_group and not event.is_channel:
                return

            message_text = event.message.text.lower() if hasattr(event.message, 'text') else ''
            group_id = event.chat_id
            
            logger.debug(f"Received message in group {group_id}: {message_text[:30]}...")
            
            # Check cooldown period
            current_time = time.time()
            if group_id in self.last_responses:
                time_since_last = current_time - self.last_responses[group_id]
                if time_since_last < rate_limits['keyword_cooldown']:
                    logger.debug(f"Skipping response due to cooldown. {time_since_last:.1f}s since last response.")
                    return

            # Check for keywords
            keywords_found = []
            if 'کرمانشاه' in message_text:
                keywords_found.append('kermanshah')
            if 'موبایل' in message_text:
                keywords_found.append('mobile')
            if 'گوشی' in message_text:
                keywords_found.append('phone')

            if keywords_found:
                logger.info(f"Keywords found in group {group_id}: {keywords_found}")
                
                for keyword in keywords_found:
                    await self.queue_response(event, keyword)
                    self.last_responses[group_id] = current_time
                    self.update_stats(group_id, keyword)
            
        except Exception as e:
            logger.error(f"Error in smart_message_handler: {str(e)}", exc_info=True)

    async def queue_response(self, event, keyword):
        """Queue a response for a specific keyword"""
        try:
            if keyword in messages:
                messages_for_keyword = sorted(messages[keyword], key=lambda x: x['priority'])
                for msg in messages_for_keyword:
                    await self.message_queue.put({
                        'event': event,
                        'message': msg['text'],
                        'keyword': keyword
                    })
                    logger.debug(f"Queued message for keyword '{keyword}'")
            else:
                logger.warning(f"No messages defined for keyword: {keyword}")
        except Exception as e:
            logger.error(f"Error in queue_response: {str(e)}", exc_info=True)

    async def message_sender(self):
        """Send messages from the queue with rate limiting"""
        logger.info("Message sender task started")
        while True:
            try:
                if self.message_queue.empty():
                    await asyncio.sleep(1)
                    continue
                    
                message_data = await self.message_queue.get()
                event = message_data['event']
                message = message_data['message']
                keyword = message_data['keyword']
                
                logger.info(f"Sending message for keyword '{keyword}' in group {event.chat_id}")
                await self.smart_send_message(event, message)
                
                # Random delay between messages to appear more human
                delay = random.uniform(30, 60)
                logger.debug(f"Waiting {delay:.1f}s before next message")
                await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(f"Error in message_sender: {str(e)}", exc_info=True)
            finally:
                # Mark as done even if there was an error
                self.message_queue.task_done()

    async def smart_send_message(self, event, message):
        """Send a message with error handling"""
        try:
            # Get group info for better logging
            chat_id = event.chat_id
            chat_title = "Unknown Group"
            try:
                chat = await client.get_entity(chat_id)
                if hasattr(chat, 'title'):
                    chat_title = chat.title
            except:
                pass
                
            logger.info(f"Sending message to {chat_title} (ID: {chat_id})")
            
            # Send the message
            await event.reply(message)
            
            # Update last activity timestamp
            await self.update_group_activity(chat_id)
            
            logger.info(f"Successfully sent message to {chat_title}")
            
        except FloodWaitError as e:
            logger.warning(f"FloodWaitError: sleeping for {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Error sending message: {str(e)}", exc_info=True)

    def update_stats(self, group_id, keyword):
        """Update response statistics"""
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO stats (group_id, keyword, response_count, last_response)
                VALUES (
                    ?, ?, 
                    COALESCE((SELECT response_count + 1 FROM stats WHERE group_id = ? AND keyword = ?), 1),
                    ?
                )
            """, (group_id, keyword, group_id, keyword, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
            logger.debug(f"Updated stats for group {group_id}, keyword '{keyword}'")
        except Exception as e:
            logger.error(f"Error updating stats: {str(e)}", exc_info=True)

    async def update_group_activity(self, group_id):
        """Update the last activity timestamp for a group"""
        try:
            cursor.execute("""
                UPDATE groups 
                SET last_activity = ? 
                WHERE group_id = ?
            """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), group_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating group activity: {str(e)}", exc_info=True)

    async def cleanup_inactive_groups(self):
        """Leave inactive groups to save resources"""
        logger.info("Starting cleanup of inactive groups")
        try:
            month_ago = datetime.now() - timedelta(days=30)
            cursor.execute("SELECT group_id, name FROM groups WHERE last_activity < ?", 
                          (month_ago.strftime('%Y-%m-%d %H:%M:%S'),))
            inactive_groups = cursor.fetchall()
            
            logger.info(f"Found {len(inactive_groups)} inactive groups to clean up")
            
            for group_id, group_name in inactive_groups:
                try:
                    logger.info(f"Leaving inactive group: {group_name} (ID: {group_id})")
                    await client(LeaveChannelRequest(group_id))
                    cursor.execute("DELETE FROM groups WHERE group_id = ?", (group_id,))
                    
                    if group_id in self.joined_groups:
                        self.joined_groups.remove(group_id)
                        
                    logger.info(f"Successfully left inactive group: {group_name}")
                except Exception as e:
                    logger.error(f"Error leaving group {group_name} (ID: {group_id}): {str(e)}", exc_info=True)
            
            conn.commit()
            logger.info("Inactive group cleanup completed")
            
        except Exception as e:
            logger.error(f"Error in cleanup_inactive_groups: {str(e)}", exc_info=True)

    async def generate_report(self):
        """Generate and send a performance report to admins"""
        logger.info("Generating performance report")
        try:
            cursor.execute("""
                SELECT g.name, COUNT(s.stat_id) as responses, g.member_count, g.last_activity
                FROM groups g
                LEFT JOIN stats s ON g.group_id = s.group_id
                GROUP BY g.group_id
                ORDER BY responses DESC
            """)
            report_data = cursor.fetchall()
            
            if not report_data:
                report = "📊 گزارش عملکرد ربات:\n\nهیچ گروهی در دیتابیس ثبت نشده است."
            else:
                report = "📊 گزارش عملکرد ربات:\n\n"
                for row in report_data:
                    report += f"گروه: {row[0]}\n"
                    report += f"تعداد پاسخ‌ها: {row[1]}\n"
                    report += f"تعداد اعضا: {row[2]}\n"
                    report += f"آخرین فعالیت: {row[3]}\n"
                    report += "─────────────────\n"

            for admin_id in admin_ids:
                try:
                    await client.send_message(admin_id, report)
                    logger.info(f"Sent report to admin {admin_id}")
                except Exception as e:
                    logger.error(f"Error sending report to admin {admin_id}: {str(e)}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"Error generating report: {str(e)}", exc_info=True)

async def schedule_checker():
    """Check and run scheduled tasks"""
    logger.info("Schedule checker task started")
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logger.error(f"Error in schedule_checker: {str(e)}", exc_info=True)
        await asyncio.sleep(1)

async def main():
    """Main bot function"""
    try:
        # Setup database
        setup_database()
        
        # Create bot instance
        bot_instance = AdvancedBot()
        
        # Initialize the bot
        await bot_instance.initialize()
        
        # Register message handler
        @client.on(events.NewMessage())
        async def message_handler(event):
            await bot_instance.smart_message_handler(event)
            
        # Log existing dialogs for debugging
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                logger.debug(f"Found dialog: {dialog.name} (ID: {dialog.id})")

        # Create main tasks
        tasks = [
            asyncio.create_task(bot_instance.smart_join_groups()),
            asyncio.create_task(bot_instance.message_sender()),
            asyncio.create_task(schedule_checker())
        ]
        
        # Schedule recurring tasks
        schedule.every(2).hours.do(lambda: asyncio.create_task(bot_instance.smart_join_groups()))
        schedule.every().day.at("00:00").do(lambda: asyncio.create_task(bot_instance.cleanup_inactive_groups()))
        schedule.every().day.at("20:00").do(lambda: asyncio.create_task(bot_instance.generate_report()))
        
        logger.info("Advanced Bot started successfully!")
        print("Advanced Bot started successfully!")
        
        # Run forever
        await asyncio.gather(*tasks)
        
    except Exception as e:
        logger.error(f"Error in main function: {str(e)}", exc_info=True)

if __name__ == '__main__':
    try:
        # Start the client
        client.start()
        
        # Run the main function
        client.loop.run_until_complete(main())
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("\nBot stopped by user")
        
    except Exception as e:
        error_msg = f"Critical error occurred: {str(e)}"
        logger.error(error_msg, exc_info=True)
        print(error_msg)
        
    finally:
        # Clean up resources
        conn.close()
        client.disconnect()
