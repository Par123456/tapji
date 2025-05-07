import telebot
from telethon import TelegramClient, events, sync, functions, types
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.types import InputPeerEmpty, Channel, User
from telethon.errors import FloodWaitError, ChannelPrivateError, UserBannedInChannelError
import time
import json
import asyncio
import random
import logging
import sqlite3
from datetime import datetime, timedelta
import re
import schedule
from collections import defaultdict

# تنظیمات لاگینگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    filename='bot_log.txt'
)
logger = logging.getLogger(__name__)

# تنظیمات API
api_id = '20508268'
api_hash = '65d0d52b67f1e6d7256f22fb13864cd0'
bot_token = '7997960421:AAGvgLwhelNC4Xw9mhbdoKYGHWYud38z3Ic'
admin_ids = [5528371749]

# ایجاد کلاینت‌ها
client = TelegramClient('advanced_session', api_id, api_hash)
bot = telebot.TeleBot(bot_token)

# اتصال به دیتابیس
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

# ایجاد جداول
def setup_database():
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

# تنظیم پیام‌ها
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

# محدودیت‌ها
rate_limits = {
    'group_join': 20,
    'message_send': 30,
    'keyword_cooldown': 300
}

message_stats = defaultdict(lambda: {'count': 0, 'last_time': None})
group_stats = defaultdict(dict)

class AdvancedBot:
    def __init__(self):
        self.joined_groups = set()
        self.message_queue = asyncio.Queue()
        self.last_responses = {}
        self.load_joined_groups()

    async def smart_join_groups(self):
        """جوین شدن هوشمند در گروه‌ها با در نظر گرفتن محدودیت‌ها"""
        try:
            async for dialog in client.iter_dialogs():
                if dialog.is_group or dialog.is_channel:
                    group_name = dialog.title.lower()
                    if any(keyword in group_name for keyword in ['کرمانشاه', 'موبایل', 'گوشی']):
                        if dialog.entity.id not in self.joined_groups:
                            if await self.can_join_group(dialog.entity):
                                await self.join_and_initialize_group(dialog.entity)
        except Exception as e:
            logger.error(f"Error in smart_join_groups: {e}")

    async def can_join_group(self, group):
        """بررسی امکان جوین شدن در گروه"""
        hour_ago = datetime.now() - timedelta(hours=1)
        cursor.execute("SELECT COUNT(*) FROM groups WHERE join_date > ?", (hour_ago.strftime('%Y-%m-%d %H:%M:%S'),))
        recent_joins = cursor.fetchone()[0]
        return recent_joins < rate_limits['group_join']

    async def join_and_initialize_group(self, group):
        """جوین شدن و مقداردهی اولیه گروه"""
        try:
            await client(JoinChannelRequest(group))
            self.joined_groups.add(group.id)
            
            cursor.execute("""
                INSERT INTO groups (group_id, name, join_date, member_count, last_activity)
                VALUES (?, ?, ?, ?, ?)
            """, (group.id, group.title, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                  group.participants_count if hasattr(group, 'participants_count') else 0,
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()

            logger.info(f"Successfully joined and initialized group: {group.title}")
            
        except Exception as e:
            logger.error(f"Error joining group {group.title}: {e}")

    async def smart_message_handler(self, event):
        """پردازش هوشمند پیام‌ها"""
        if not event.is_group:
            return

        message_text = event.message.text.lower()
        group_id = event.chat_id
        
        current_time = time.time()
        if group_id in self.last_responses:
            if current_time - self.last_responses[group_id] < rate_limits['keyword_cooldown']:
                return

        keywords_found = []
        if 'کرمانشاه' in message_text:
            keywords_found.append('kermanshah')
        if 'موبایل' in message_text:
            keywords_found.append('mobile')
        if 'گوشی' in message_text:
            keywords_found.append('phone')

        for keyword in keywords_found:
            await self.queue_response(event, keyword)
            self.last_responses[group_id] = current_time
            self.update_stats(group_id, keyword)

    async def queue_response(self, event, keyword):
        """قرار دادن پیام در صف ارسال"""
        messages_for_keyword = sorted(messages[keyword], key=lambda x: x['priority'])
        for msg in messages_for_keyword:
            await self.message_queue.put({
                'event': event,
                'message': msg['text'],
                'keyword': keyword
            })

    async def message_sender(self):
        """ارسال پیام‌ها از صف با رعایت محدودیت‌ها"""
        while True:
            try:
                message_data = await self.message_queue.get()
                event = message_data['event']
                message = message_data['message']
                
                await self.smart_send_message(event, message)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                
            except Exception as e:
                logger.error(f"Error in message_sender: {e}")
            finally:
                self.message_queue.task_done()

    async def smart_send_message(self, event, message):
        """ارسال هوشمند پیام با مدیریت خطا"""
        try:
            await event.reply(message)
            await self.update_group_activity(event.chat_id)
        except FloodWaitError as e:
            logger.warning(f"FloodWaitError: sleeping for {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Error sending message: {e}")

    def update_stats(self, group_id, keyword):
        """بروزرسانی آمار"""
        cursor.execute("""
            INSERT OR REPLACE INTO stats (group_id, keyword, response_count, last_response)
            VALUES (
                ?, ?, 
                COALESCE((SELECT response_count + 1 FROM stats WHERE group_id = ? AND keyword = ?), 1),
                ?
            )
        """, (group_id, keyword, group_id, keyword, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()

    async def update_group_activity(self, group_id):
        """بروزرسانی فعالیت گروه"""
        cursor.execute("""
            UPDATE groups 
            SET last_activity = ? 
            WHERE group_id = ?
        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), group_id))
        conn.commit()

    async def cleanup_inactive_groups(self):
        """پاکسازی گروه‌های غیرفعال"""
        month_ago = datetime.now() - timedelta(days=30)
        cursor.execute("SELECT group_id FROM groups WHERE last_activity < ?", 
                      (month_ago.strftime('%Y-%m-%d %H:%M:%S'),))
        inactive_groups = cursor.fetchall()
        
        for group_id in inactive_groups:
            try:
                await client(LeaveChannelRequest(group_id[0]))
                cursor.execute("DELETE FROM groups WHERE group_id = ?", (group_id[0],))
                self.joined_groups.remove(group_id[0])
                logger.info(f"Left inactive group: {group_id[0]}")
            except Exception as e:
                logger.error(f"Error leaving group {group_id[0]}: {e}")
        
        conn.commit()

    def load_joined_groups(self):
        """بارگذاری لیست گروه‌های جوین شده"""
        cursor.execute("SELECT group_id FROM groups")
        self.joined_groups = set(row[0] for row in cursor.fetchall())

    async def generate_report(self):
        """تولید گزارش عملکرد"""
        cursor.execute("""
            SELECT g.name, COUNT(s.stat_id) as responses, g.member_count, g.last_activity
            FROM groups g
            LEFT JOIN stats s ON g.group_id = s.group_id
            GROUP BY g.group_id
            ORDER BY responses DESC
        """)
        report_data = cursor.fetchall()
        
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
            except Exception as e:
                logger.error(f"Error sending report to admin {admin_id}: {e}")

async def schedule_checker():
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)

async def main():
    # راه‌اندازی دیتابیس
    setup_database()
    
    # ایجاد نمونه از بات
    bot_instance = AdvancedBot()
    
    # ثبت هندلر پیام‌ها
    @client.on(events.NewMessage(pattern='.*'))
    async def message_handler(event):
        await bot_instance.smart_message_handler(event)

    # ایجاد تسک‌های اصلی
    tasks = [
        asyncio.create_task(bot_instance.smart_join_groups()),
        asyncio.create_task(bot_instance.message_sender()),
        asyncio.create_task(schedule_checker())
    ]

    # تنظیم زمانبندی‌ها
    schedule.every().hour.do(lambda: asyncio.create_task(bot_instance.smart_join_groups()))
    schedule.every().day.at("00:00").do(lambda: asyncio.create_task(bot_instance.cleanup_inactive_groups()))
    schedule.every().day.at("20:00").do(lambda: asyncio.create_task(bot_instance.generate_report()))

    print("Advanced Bot started successfully!")
    
    # اجرای همه تسک‌ها
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    try:
        # راه‌اندازی کلاینت
        client.start()
        
        # اجرای لوپ اصلی
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"Error occurred: {e}")
        logger.error(f"Critical error: {e}")
    finally:
        conn.close()
        client.disconnect()
