import os
import asyncio
import logging
import hashlib
import re
import json
import tempfile
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Set, Any
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command, BaseFilter
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import LabeledPrice, PreCheckoutQuery, ChatJoinRequest, ChatMemberUpdated



# Р—Р°РіСЂСѓР·РєР° РїРµСЂРµРјРµРЅРЅС‹С… РѕРєСЂСѓР¶РµРЅРёСЏ
load_dotenv()

# ============================================
# РќРђРЎРўР РћР™РљР
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SECONDARY_CHAT_ID = os.getenv("SECONDARY_CHAT_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
DELAY_SECONDS = int(os.getenv("DELAY_SECONDS", 180))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", 5))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", 20))
PAGE_RETRY_COUNT = int(os.getenv("PAGE_RETRY_COUNT", 3))
MAX_PAGES = int(os.getenv("MAX_PAGES", 50))
PAGE_DELAY_SECONDS = float(os.getenv("PAGE_DELAY_SECONDS", 0.7))
MIN_SNAPSHOT_RATIO = float(os.getenv("MIN_SNAPSHOT_RATIO", 0.6))
ANOMALY_COOLDOWN_MINUTES = int(os.getenv("ANOMALY_COOLDOWN_MINUTES", 30))

# Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ РђСЃС‚Р°РЅР°
ASTANA_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET))

# РџСѓС‚СЊ Рє С„Р°Р№Р»Сѓ СЃРѕС…СЂР°РЅРµРЅРёСЏ
DATA_FILE = "vacancies.json"
SUBS_FILE = "subscriptions.json"

# РќР°СЃС‚СЂРѕР№РєРё РїРѕРґРїРёСЃРєРё
MOMENTUM_PRO_CHANNEL_ID = -1003836921999
STARS_PRICE = 250
SUBSCRIPTION_DAYS = 30

# ============================================
# Р›РћР“РР РћР’РђРќРР•
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def atomic_json_dump(path: str, payload: Any) -> None:
    """Safely write JSON to disk to avoid corrupting state on crashes."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temp_path = tempfile.mkstemp(prefix="tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, ensure_ascii=False, indent=4)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

def get_astana_time() -> datetime:
    """РџРѕР»СѓС‡РёС‚СЊ С‚РµРєСѓС‰РµРµ РІСЂРµРјСЏ РІ С‡Р°СЃРѕРІРѕРј РїРѕСЏСЃРµ РђСЃС‚Р°РЅС‹"""
    return datetime.now(ASTANA_TZ)

# ============================================
# РЈРџР РђР’Р›Р•РќРР• РџРћР”РџРРЎРљРђРњР
# ============================================

class SubscriptionManager:
    def __init__(self):
        self.subscriptions: Dict[str, str] = {}
        self.load_subs()

    def load_subs(self):
        """Р—Р°РіСЂСѓР·РєР° РїРѕРґРїРёСЃРѕРє РёР· С„Р°Р№Р»Р°"""
        if os.path.exists(SUBS_FILE):
            try:
                with open(SUBS_FILE, 'r', encoding='utf-8') as f:
                    self.subscriptions = json.load(f)
            except Exception as e:
                logger.error(f"РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё РїРѕРґРїРёСЃРѕРє: {e}")
                self.subscriptions = {}
        else:
            self.subscriptions = {}

    def save_subs(self):
        """РЎРѕС…СЂР°РЅРµРЅРёРµ РїРѕРґРїРёСЃРѕРє РІ С„Р°Р№Р»"""
        try:
            with open(SUBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.subscriptions, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ РїРѕРґРїРёСЃРѕРє: {e}")

    def add_subscription(self, user_id: int, days: int = SUBSCRIPTION_DAYS):
        """Р”РѕР±Р°РІРёС‚СЊ РёР»Рё РїСЂРѕРґР»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ"""
        user_id_str = str(user_id)
        now = get_astana_time()
        
        if user_id_str in self.subscriptions:
            current_expiry = datetime.fromisoformat(self.subscriptions[user_id_str])
            if current_expiry > now:
                new_expiry = current_expiry + timedelta(days=days)
            else:
                new_expiry = now + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)
            
        self.subscriptions[user_id_str] = new_expiry.isoformat()
        self.save_subs()
        return new_expiry

    def is_active(self, user_id: int) -> bool:
        """РџСЂРѕРІРµСЂРёС‚СЊ, Р°РєС‚РёРІРЅР° Р»Рё РїРѕРґРїРёСЃРєР°"""
        user_id_str = str(user_id)
        if user_id_str not in self.subscriptions:
            return False
            
        expiry = datetime.fromisoformat(self.subscriptions[user_id_str])
        return expiry > get_astana_time()

    def get_expiry(self, user_id: int) -> Optional[datetime]:
        """РџРѕР»СѓС‡РёС‚СЊ РґР°С‚Сѓ РёСЃС‚РµС‡РµРЅРёСЏ РїРѕРґРїРёСЃРєРё"""
        user_id_str = str(user_id)
        if user_id_str in self.subscriptions:
            return datetime.fromisoformat(self.subscriptions[user_id_str])
        return None

# ============================================
# РњРћРќРРўРћР  Р’РђРљРђРќРЎРР™
# ============================================

class VacancyMonitor:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.previous_vacancies: Dict[str, Dict] = {}
        self.delivery_history: Set[str] = set()
        self.base_url = "https://agropraktika.eu"
        self.check_interval = CHECK_INTERVAL
        
        # РЎС‚Р°С‚РёСЃС‚РёРєР°
        self.start_time = get_astana_time()
        self.check_count = 0
        self.last_check_time = None
        self.running = True
        self.last_hourly_report = None
        self.last_anomaly_alert = None
        self.last_snapshot_meta: Dict[str, Any] = {}
        
        # Р—Р°РіСЂСѓР·РєР° РґР°РЅРЅС‹С…
        self.load_data()

    def load_data(self):
        """Р—Р°РіСЂСѓР·РєР° СЃРѕС…СЂР°РЅРµРЅРЅС‹С… РІР°РєР°РЅСЃРёР№ РёР· С„Р°Р№Р»Р°"""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    raw_data = json.load(f)

                if isinstance(raw_data, dict) and "vacancies" in raw_data:
                    self.previous_vacancies = raw_data.get("vacancies", {})
                    self.delivery_history = set(raw_data.get("delivery_history", []))
                    self.last_snapshot_meta = raw_data.get("meta", {})
                else:
                    self.previous_vacancies = raw_data if isinstance(raw_data, dict) else {}
                    self.delivery_history = set()
                    self.last_snapshot_meta = {}
                logger.info(f"Р—Р°РіСЂСѓР¶РµРЅРѕ {len(self.previous_vacancies)} РІР°РєР°РЅСЃРёР№ РёР· {DATA_FILE}")
            except Exception as e:
                logger.error(f"РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё РґР°РЅРЅС‹С…: {e}")
                self.previous_vacancies = {}
                self.delivery_history = set()
                self.last_snapshot_meta = {}
        else:
            self.previous_vacancies = {}
            self.delivery_history = set()
            self.last_snapshot_meta = {}

    def save_data(self):
        """РЎРѕС…СЂР°РЅРµРЅРёРµ РІР°РєР°РЅСЃРёР№ РІ С„Р°Р№Р»"""
        try:
            payload = {
                "schema_version": 2,
                "vacancies": self.previous_vacancies,
                "delivery_history": sorted(self.delivery_history),
                "meta": self.last_snapshot_meta,
            }
            atomic_json_dump(DATA_FILE, payload)
        except Exception as e:
            logger.error(f"РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ РґР°РЅРЅС‹С…: {e}")

    def normalize_text(self, value: Optional[str]) -> str:
        """Normalize whitespace to make text checks more robust."""
        return re.sub(r"\s+", " ", value or "").strip()

    def status_is_suspended(self, status: Optional[str]) -> bool:
        """Detect suspended/closed registration with fuzzy matching."""
        normalized = self.normalize_text(status).lower()
        suspended_markers = (
            "РїСЂРёРѕСЃС‚Р°РЅРѕРІ",
            "РІСЂРµРјРµРЅРЅРѕ Р·Р°РєСЂС‹С‚",
            "Р·Р°РєРѕРЅС‡",
            "РјРµСЃС‚ РЅРµС‚",
            "closed",
            "suspended",
        )
        return any(marker in normalized for marker in suspended_markers)

    def build_delivery_key(self, vacancy: Dict) -> str:
        """Build a stable key for deduplicating alert delivery."""
        return f"{vacancy['id']}|{self.normalize_text(vacancy.get('status', '')).lower()}"

    def format_uptime(self) -> str:
        """Р¤РѕСЂРјР°С‚РёСЂРѕРІР°РЅРёРµ РІСЂРµРјРµРЅРё СЂР°Р±РѕС‚С‹"""
        uptime = get_astana_time() - self.start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if days > 0: parts.append(f"{days} РґРЅ.")
        if hours > 0: parts.append(f"{hours} С‡.")
        if minutes > 0: parts.append(f"{minutes} РјРёРЅ.")
        if not parts: parts.append(f"{seconds} СЃРµРє.")
            
        return " ".join(parts)

    def get_status_message(self) -> str:
        """Р“РµРЅРµСЂР°С†РёСЏ СЃРѕРѕР±С‰РµРЅРёСЏ СЃС‚Р°С‚СѓСЃР°"""
        current_time = get_astana_time()
        total_vacancies = len(self.previous_vacancies)
        suspended = sum(1 for v in self.previous_vacancies.values() if self.status_is_suspended(v.get('status', '')))
        active = total_vacancies - suspended
        
        if self.last_check_time:
            next_check = self.last_check_time + timedelta(seconds=self.check_interval)
            time_to_next = next_check - current_time
            if time_to_next.total_seconds() > 0:
                next_check_str = f"{int(time_to_next.total_seconds() // 60)} РјРёРЅ. {int(time_to_next.total_seconds() % 60)} СЃРµРє."
            else:
                next_check_str = "СЃРєРѕСЂРѕ..."
        else:
            next_check_str = "РѕР¶РёРґР°РЅРёРµ..."
        
        return f"""
рџ¤– <b>РЎС‚Р°С‚СѓСЃ Р±РѕС‚Р° Momentum</b>

вњ… <b>РЎС‚Р°С‚СѓСЃ:</b> Р Р°Р±РѕС‚Р°РµС‚
вЏ° <b>Р’СЂРµРјСЏ СЂР°Р±РѕС‚С‹:</b> {self.format_uptime()}
рџ“… <b>Р—Р°РїСѓС‰РµРЅ:</b> {self.start_time.strftime('%H:%M:%S %d.%m.%Y')} (РђСЃС‚Р°РЅР°)

рџ“Љ <b>РЎС‚Р°С‚РёСЃС‚РёРєР°:</b>
в”њ Р’Р°РєР°РЅСЃРёР№ РѕС‚СЃР»РµР¶РёРІР°РµС‚СЃСЏ: <b>{total_vacancies}</b>
в”њ рџ”ґ РџСЂРёРѕСЃС‚Р°РЅРѕРІР»РµРЅРѕ: <b>{suspended}</b>
в”њ рџџў РђРєС‚РёРІРЅС‹С…: <b>{active}</b>
в”” #пёЏвѓЈ РџСЂРѕРІРµСЂРѕРє РІС‹РїРѕР»РЅРµРЅРѕ: <b>{self.check_count}</b>

вЏ± <b>РџСЂРѕРІРµСЂРєРё:</b>
в”њ РџРѕСЃР»РµРґРЅСЏСЏ: {self.last_check_time.strftime('%H:%M:%S') if self.last_check_time else 'РµС‰С‘ РЅРµ Р±С‹Р»Рѕ'} (РђСЃС‚Р°РЅР°)
в”њ РЎР»РµРґСѓСЋС‰Р°СЏ С‡РµСЂРµР·: {next_check_str}
в”” РРЅС‚РµСЂРІР°Р»: {self.check_interval // 60} РјРёРЅ.

рџЊђ <b>РЎР°Р№С‚:</b> agropraktika.eu/vacancies
"""

    def extract_vacancies_from_html(self, html: str, page: int) -> Tuple[List[Dict], Dict[str, Any]]:
        """Parse vacancies from HTML with fallback selectors."""
        soup = BeautifulSoup(html, 'html.parser')
        vacancy_items = soup.select('ul.vacancies-list li.vacancy-item')

        if not vacancy_items:
            vacancy_items = [
                node for node in soup.select('a[href*="/vacancies/"]')
                if "/vacancies/" in (node.get("href") or "")
            ]

        seen_ids = set()
        vacancies: List[Dict] = []
        parse_errors = 0

        for item in vacancy_items:
            try:
                link_tag = item if getattr(item, "name", None) == "a" else item.select_one('a[href*="/vacancies/"]')
                if not link_tag:
                    continue

                href = (link_tag.get('href') or '').strip()
                if not href:
                    continue

                link = urljoin(self.base_url, href)
                title = self.normalize_text(link_tag.get_text(" ", strip=True))
                if not title:
                    title = self.normalize_text(item.get_text(" ", strip=True))

                text_content = self.normalize_text(item.get_text(" ", strip=True))
                status_tag = item.select_one('p.text-red-400, .text-red-400, [class*="red"]')
                if status_tag:
                    status = self.normalize_text(status_tag.get_text(" ", strip=True))
                elif self.status_is_suspended(text_content):
                    status = "Р РµРіРёСЃС‚СЂР°С†РёСЏ РІСЂРµРјРµРЅРЅРѕ РїСЂРёРѕСЃС‚Р°РЅРѕРІР»РµРЅР°"
                else:
                    status = "Р РµРіРёСЃС‚СЂР°С†РёСЏ РѕС‚РєСЂС‹С‚Р°"

                info_blocks = item.select('div.flex.flex-wrap div.flex.items-center')
                location = ""
                if len(info_blocks) > 1:
                    location = self.normalize_text(info_blocks[1].get_text(" ", strip=True))
                if not location:
                    location_match = re.search(r'(?:Р›РѕРєР°С†РёСЏ|РњРµСЃС‚Рѕ|Location)[:\s]+(.+?)(?:РќР°С‡РёРЅР°РµС‚СЃСЏ|РѕС‚\s+\d|Р РµРіРёСЃС‚СЂР°С†РёСЏ|$)', text_content, re.IGNORECASE)
                    if location_match:
                        location = self.normalize_text(location_match.group(1))
                if not location:
                    location = "РќРµ СѓРєР°Р·Р°РЅРѕ"

                start_date = ""
                date_tag = item.select_one('div.more-information div.italic, .italic')
                date_source = self.normalize_text(date_tag.get_text(" ", strip=True)) if date_tag else text_content
                date_match = re.search(r'(\d{2}/\d{2}/\d{4})', date_source)
                if date_match:
                    start_date = date_match.group(1)

                v_id = hashlib.md5(link.encode()).hexdigest()[:8]
                if v_id in seen_ids:
                    continue

                seen_ids.add(v_id)
                vacancies.append({
                    'id': v_id,
                    'title': title or link,
                    'location': location,
                    'start_date': start_date,
                    'status': status,
                    'link': link,
                    'last_checked': get_astana_time().isoformat(),
                    'source_page': page,
                })
            except Exception as e:
                parse_errors += 1
                logger.warning(f"Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р С—Р В°РЎР‚РЎРѓР С‘Р Р…Р С–Р В° Р С•РЎвЂљР Т‘Р ВµР В»РЎРЉР Р…Р С•Р в„– Р Р†Р В°Р С”Р В°Р Р…РЎРѓР С‘Р С‘: {e}")

        diagnostics = {
            "page": page,
            "cards_found": len(vacancy_items),
            "vacancies_found": len(vacancies),
            "parse_errors": parse_errors,
            "html_length": len(html),
        }
        return vacancies, diagnostics

    async def fetch_vacancies_safe(self, session: aiohttp.ClientSession, page: int = 1) -> Tuple[List[Dict], Dict[str, Any]]:
        """Fetch one page with retries and diagnostics."""
        url = f"{self.base_url}/vacancies?page={page}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://agropraktika.eu/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

        last_error = None
        for attempt in range(1, PAGE_RETRY_COUNT + 1):
            try:
                async with session.get(url, headers=headers, timeout=HTTP_TIMEOUT) as response:
                    if response.status != 200:
                        last_error = f"HTTP {response.status}"
                        logger.error(f"Р С›РЎв‚¬Р С‘Р В±Р С”Р В° РЎРѓР В°Р в„–РЎвЂљР В° {url}: {response.status}")
                    else:
                        html = await response.text(errors='ignore')
                        vacancies, diagnostics = self.extract_vacancies_from_html(html, page)
                        diagnostics.update({"url": url, "attempt": attempt, "success": True})
                        return vacancies, diagnostics
            except Exception as e:
                last_error = str(e)
                logger.error(f"Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р С—РЎР‚Р С‘ Р В·Р В°Р С—РЎР‚Р С•РЎРѓР Вµ Р С” {url} (Р С—Р С•Р С—РЎвЂ№РЎвЂљР С”Р В° {attempt}/{PAGE_RETRY_COUNT}): {e}")

            if attempt < PAGE_RETRY_COUNT:
                await asyncio.sleep(min(2 ** (attempt - 1), 6))

        return [], {
            "page": page,
            "url": url,
            "attempt": PAGE_RETRY_COUNT,
            "success": False,
            "error": last_error or "unknown_error",
        }

    def snapshot_is_healthy(self, snapshot: Dict[str, Any]) -> bool:
        """Reject suspiciously incomplete snapshots to avoid data loss."""
        current_total = snapshot["total_vacancies"]
        previous_total = len(self.previous_vacancies)

        if snapshot["pages_failed"] > 0:
            return False
        if snapshot["pages_succeeded"] == 0:
            return False
        if current_total == 0 and previous_total > 0:
            return False
        if previous_total > 0 and current_total < max(1, int(previous_total * MIN_SNAPSHOT_RATIO)):
            return False
        return True

    async def alert_admin(self, message: str) -> None:
        """Send anomaly alerts to the admin with cooldown."""
        if not ADMIN_ID:
            return

        now = get_astana_time()
        if self.last_anomaly_alert and now - self.last_anomaly_alert < timedelta(minutes=ANOMALY_COOLDOWN_MINUTES):
            return

        try:
            await self.bot.send_message(ADMIN_ID, message, parse_mode=ParseMode.HTML)
            self.last_anomaly_alert = now
        except Exception as e:
            logger.error(f"Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р С•РЎвЂљР С—РЎР‚Р В°Р Р†Р С”Р С‘ Р В°Р В»Р ВµРЎР‚РЎвЂљР В° Р В°Р Т‘Р СР С‘Р Р…РЎС“: {e}")

    async def collect_snapshot(self, session: aiohttp.ClientSession) -> Dict[str, Any]:
        """Collect a validated snapshot across pages."""
        all_vacancies: List[Dict] = []
        page_diagnostics: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        empty_pages_in_a_row = 0
        stagnant_pages_in_a_row = 0

        for page in range(1, MAX_PAGES + 1):
            vacs, diagnostics = await self.fetch_vacancies_safe(session, page)
            page_diagnostics.append(diagnostics)

            if not diagnostics.get("success"):
                break

            if not vacs:
                empty_pages_in_a_row += 1
                if empty_pages_in_a_row >= 2:
                    break
                continue

            empty_pages_in_a_row = 0
            page_new_items = 0
            for vacancy in vacs:
                if vacancy["id"] in seen_ids:
                    continue
                seen_ids.add(vacancy["id"])
                all_vacancies.append(vacancy)
                page_new_items += 1

            if page_new_items == 0:
                stagnant_pages_in_a_row += 1
                if stagnant_pages_in_a_row >= 2:
                    break
            else:
                stagnant_pages_in_a_row = 0

            if PAGE_DELAY_SECONDS > 0:
                await asyncio.sleep(PAGE_DELAY_SECONDS)

        pages_failed = sum(1 for item in page_diagnostics if not item.get("success"))
        pages_succeeded = sum(1 for item in page_diagnostics if item.get("success"))
        parse_errors = sum(int(item.get("parse_errors", 0)) for item in page_diagnostics)

        snapshot = {
            "vacancies": all_vacancies,
            "total_vacancies": len(all_vacancies),
            "pages_checked": len(page_diagnostics),
            "pages_succeeded": pages_succeeded,
            "pages_failed": pages_failed,
            "parse_errors": parse_errors,
            "page_diagnostics": page_diagnostics,
            "collected_at": get_astana_time().isoformat(),
        }
        snapshot["healthy"] = self.snapshot_is_healthy(snapshot)
        return snapshot

    async def analyze_changes(self, current_vacancies: List[Dict]):
        """Analyze only confirmed snapshots and deduplicate delivery."""
        current_dict = {v['id']: v for v in current_vacancies}
        new_openings = []

        for v_id, vac in current_dict.items():
            if v_id not in self.previous_vacancies:
                if not self.status_is_suspended(vac['status']):
                    new_openings.append(vac)
            else:
                old_status = self.previous_vacancies[v_id]['status']
                new_status = vac['status']
                if self.status_is_suspended(old_status) and not self.status_is_suspended(new_status):
                    new_openings.append(vac)

        for vac in new_openings:
            delivery_key = self.build_delivery_key(vac)
            if delivery_key in self.delivery_history:
                continue

            msg = f"""
СЂСџСџСћ <b>Р вЂ™Р С’Р вЂ“Р СњР С›: Р В Р ВµР С–Р С‘РЎРѓРЎвЂљРЎР‚Р В°РЎвЂ Р С‘РЎРЏ Р С•РЎвЂљР С”РЎР‚РЎвЂ№Р В»Р В°РЎРѓРЎРЉ!</b>

СЂСџРЏВ· <b>Р вЂ™Р В°Р С”Р В°Р Р…РЎРѓР С‘РЎРЏ:</b> {vac['title']}
СЂСџвЂњРЊ <b>Р СљР ВµРЎРѓРЎвЂљР С•:</b> {vac['location']}
СЂСџС™Р‚ <b>Р СњР В°РЎвЂЎР С‘Р Р…Р В°Р ВµРЎвЂљРЎРѓРЎРЏ:</b> {vac['start_date']}

СЂСџвЂќвЂ” <a href="{vac['link']}">Р РЋР С”Р С•РЎР‚Р ВµР Вµ Р С—Р ВµРЎР‚Р ВµРЎвЂ¦Р С•Р Т‘Р С‘ Р С—Р С• РЎРѓРЎРѓРЎвЂ№Р В»Р С”Р Вµ!</a>

СЂСџвЂўС’ Р вЂ™РЎР‚Р ВµР СРЎРЏ: {get_astana_time().strftime('%H:%M:%S %d.%m.%Y')} (Р С’РЎРѓРЎвЂљР В°Р Р…Р В°)
"""
            try:
                await self.bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode=ParseMode.HTML)
                self.delivery_history.add(delivery_key)
                logger.info(f"Р С›РЎвЂљР С—РЎР‚Р В°Р Р†Р В»Р ВµР Р…Р С• РЎС“Р Р†Р ВµР Т‘Р С•Р СР В»Р ВµР Р…Р С‘Р Вµ Р Р† Р С•РЎРѓР Р…Р С•Р Р†Р Р…Р С•Р в„– Р С”Р В°Р Р…Р В°Р В»: {vac['title']}")
            except Exception as e:
                logger.error(f"Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р С•РЎвЂљР С—РЎР‚Р В°Р Р†Р С”Р С‘ Р Р† Р С•РЎРѓР Р…Р С•Р Р†Р Р…Р С•Р в„– Р С”Р В°Р Р…Р В°Р В»: {e}")
                await self.alert_admin(
                    "<b>РЎР±РѕР№ РґРѕСЃС‚Р°РІРєРё СѓРІРµРґРѕРјР»РµРЅРёСЏ</b>\n\n"
                    f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РІР°РєР°РЅСЃРёСЋ:\n<b>{vac['title']}</b>\n{vac['link']}"
                )

            if SECONDARY_CHAT_ID:
                asyncio.create_task(self.delayed_notification(SECONDARY_CHAT_ID, msg, vac['title']))

        self.previous_vacancies = current_dict
        self.save_data()

    async def delayed_notification(self, chat_id: str, message: str, vacancy_title: str):
        """Send delayed notification with retries."""
        logger.info(f"Р вЂ”Р В°Р С—Р В»Р В°Р Р…Р С‘РЎР‚Р С•Р Р†Р В°Р Р…Р С• Р С•РЎвЂљР В»Р С•Р В¶Р ВµР Р…Р Р…Р С•Р Вµ РЎС“Р Р†Р ВµР Т‘Р С•Р СР В»Р ВµР Р…Р С‘Р Вµ ({DELAY_SECONDS}РЎРѓ) Р Т‘Р В»РЎРЏ: {vacancy_title}")
        await asyncio.sleep(DELAY_SECONDS)
        for attempt in range(1, PAGE_RETRY_COUNT + 1):
            try:
                await self.bot.send_message(chat_id, message, parse_mode=ParseMode.HTML)
                logger.info(f"Р С›РЎвЂљР С—РЎР‚Р В°Р Р†Р В»Р ВµР Р…Р С• Р С•РЎвЂљР В»Р С•Р В¶Р ВµР Р…Р Р…Р С•Р Вµ РЎС“Р Р†Р ВµР Т‘Р С•Р СР В»Р ВµР Р…Р С‘Р Вµ Р Р†Р С• Р Р†РЎвЂљР С•РЎР‚Р С•Р в„– Р С”Р В°Р Р…Р В°Р В»: {vacancy_title}")
                return
            except Exception as e:
                logger.error(f"Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р С•РЎвЂљР С—РЎР‚Р В°Р Р†Р С”Р С‘ Р С•РЎвЂљР В»Р С•Р В¶Р ВµР Р…Р Р…Р С•Р С–Р С• РЎС“Р Р†Р ВµР Т‘Р С•Р СР В»Р ВµР Р…Р С‘РЎРЏ (Р С—Р С•Р С—РЎвЂ№РЎвЂљР С”Р В° {attempt}/{PAGE_RETRY_COUNT}): {e}")
                if attempt < PAGE_RETRY_COUNT:
                    await asyncio.sleep(min(2 ** (attempt - 1), 6))

    async def monitor_loop(self):
        """Safer monitoring loop that only trusts healthy snapshots."""
        logger.info("Р В¦Р С‘Р С”Р В» Р СР С•Р Р…Р С‘РЎвЂљР С•РЎР‚Р С‘Р Р…Р С–Р В° Р В·Р В°Р С—РЎС“РЎвЂ°Р ВµР Р…")

        if not self.previous_vacancies:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=HTTP_TIMEOUT, sock_read=HTTP_TIMEOUT)
            connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                initial_snapshot = await self.collect_snapshot(session)

            if initial_snapshot["healthy"]:
                self.previous_vacancies = {v['id']: v for v in initial_snapshot["vacancies"]}
                self.last_snapshot_meta = {
                    "total_vacancies": initial_snapshot["total_vacancies"],
                    "pages_checked": initial_snapshot["pages_checked"],
                    "pages_succeeded": initial_snapshot["pages_succeeded"],
                    "pages_failed": initial_snapshot["pages_failed"],
                    "parse_errors": initial_snapshot["parse_errors"],
                    "collected_at": initial_snapshot["collected_at"],
                }
                self.save_data()
                logger.info(f"Р ВР Р…Р С‘РЎвЂ Р С‘Р В°Р В»Р С‘Р В·Р В°РЎвЂ Р С‘РЎРЏ: РЎРѓР С•РЎвЂ¦РЎР‚Р В°Р Р…Р ВµР Р…Р С• {len(initial_snapshot['vacancies'])} Р Р†Р В°Р С”Р В°Р Р…РЎРѓР С‘Р в„–")
            else:
                logger.warning("Р ВР Р…Р С‘РЎвЂ Р С‘Р В°Р В»РЎРЉР Р…РЎвЂ№Р в„– РЎРѓР Р…Р С‘Р СР С•Р С” РЎРѓР В°Р в„–РЎвЂљР В° Р Р…Р Вµ Р С—Р С•Р Т‘РЎвЂљР Р†Р ВµРЎР‚Р В¶Р Т‘РЎвЂР Р…, РЎРѓР С•РЎРѓРЎвЂљР С•РЎРЏР Р…Р С‘Р Вµ Р Р…Р Вµ РЎРѓР С•РЎвЂ¦РЎР‚Р В°Р Р…Р ВµР Р…Р С•")

        while self.running:
            try:
                self.last_check_time = get_astana_time()
                self.check_count += 1
                logger.info(f"Р СџРЎР‚Р С•Р Р†Р ВµРЎР‚Р С”Р В° #{self.check_count}...")

                timeout = aiohttp.ClientTimeout(total=None, sock_connect=HTTP_TIMEOUT, sock_read=HTTP_TIMEOUT)
                connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
                async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                    snapshot = await self.collect_snapshot(session)

                if not snapshot["vacancies"]:
                    logger.warning("Р СњР Вµ РЎС“Р Т‘Р В°Р В»Р С•РЎРѓРЎРЉ Р С—Р С•Р В»РЎС“РЎвЂЎР С‘РЎвЂљРЎРЉ Р Т‘Р В°Р Р…Р Р…РЎвЂ№Р Вµ Р С• Р Р†Р В°Р С”Р В°Р Р…РЎРѓР С‘РЎРЏРЎвЂ¦")
                elif not snapshot["healthy"]:
                    logger.warning(
                        "Р СџР С•Р Т‘Р С•Р В·РЎР‚Р С‘РЎвЂљР ВµР В»РЎРЉР Р…РЎвЂ№Р в„– РЎРѓР Р…Р С‘Р СР С•Р С” РЎРѓР В°Р в„–РЎвЂљР В°: total=%s success=%s failed=%s parse_errors=%s prev_total=%s",
                        snapshot["total_vacancies"],
                        snapshot["pages_succeeded"],
                        snapshot["pages_failed"],
                        snapshot["parse_errors"],
                        len(self.previous_vacancies),
                    )
                    await self.alert_admin(
                        "<b>РђРЅРѕРјР°Р»РёСЏ РјРѕРЅРёС‚РѕСЂРёРЅРіР° Agropraktika</b>\n\n"
                        f"РЎРЅРёРјРѕРє СЃР°Р№С‚Р° РЅРµ РїРѕРґС‚РІРµСЂР¶РґС‘РЅ.\n"
                        f"Р’Р°РєР°РЅСЃРёР№ СЃРѕР±СЂР°РЅРѕ: <b>{snapshot['total_vacancies']}</b>\n"
                        f"РЎС‚СЂР°РЅРёС† СѓСЃРїРµС€РЅРѕ: <b>{snapshot['pages_succeeded']}</b>\n"
                        f"РЎС‚СЂР°РЅРёС† СЃ РѕС€РёР±РєРѕР№: <b>{snapshot['pages_failed']}</b>\n"
                        f"РћС€РёР±РѕРє РїР°СЂСЃРёРЅРіР°: <b>{snapshot['parse_errors']}</b>\n"
                        f"Р’ РїСЂРѕС€Р»РѕР№ РїРѕРґС‚РІРµСЂР¶РґС‘РЅРЅРѕР№ РІРµСЂСЃРёРё Р±С‹Р»Рѕ: <b>{len(self.previous_vacancies)}</b>"
                    )
                else:
                    self.last_snapshot_meta = {
                        "total_vacancies": snapshot["total_vacancies"],
                        "pages_checked": snapshot["pages_checked"],
                        "pages_succeeded": snapshot["pages_succeeded"],
                        "pages_failed": snapshot["pages_failed"],
                        "parse_errors": snapshot["parse_errors"],
                        "collected_at": snapshot["collected_at"],
                    }
                    await self.analyze_changes(snapshot["vacancies"])

                await self.check_hourly_report()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р Р† РЎвЂ Р С‘Р С”Р В»Р Вµ Р СР С•Р Р…Р С‘РЎвЂљР С•РЎР‚Р С‘Р Р…Р С–Р В°: {e}")
                await asyncio.sleep(60)

    async def check_hourly_report(self):
        """РћС‚РїСЂР°РІРєР° РµР¶РµС‡Р°СЃРЅРѕРіРѕ РѕС‚С‡РµС‚Р° С‚РѕР»СЊРєРѕ РІРѕ РІС‚РѕСЂРѕР№ РєР°РЅР°Р» (РІ Momentum Pro - С‚РѕР»СЊРєРѕ СѓРІРµРґРѕРјР»РµРЅРёСЏ Рѕ РІР°РєР°РЅСЃРёСЏС…)"""
        now = get_astana_time()
        if self.last_hourly_report is None:
            self.last_hourly_report = now
            return

        if now - self.last_hourly_report >= timedelta(hours=1):
            report = self.get_status_message()

            # РћС‚РїСЂР°РІРєР° С‚РѕР»СЊРєРѕ РІРѕ РІС‚РѕСЂРѕР№ РєР°РЅР°Р» (РІ Momentum Pro РѕС‚С‡РµС‚С‹ РЅРµ РѕС‚РїСЂР°РІР»СЏРµРј)
            if SECONDARY_CHAT_ID:
                try:
                    await self.bot.send_message(SECONDARY_CHAT_ID, report, parse_mode=ParseMode.HTML)
                    logger.info("РћС‚РїСЂР°РІР»РµРЅ РµР¶РµС‡Р°СЃРЅС‹Р№ РѕС‚С‡РµС‚ РІРѕ РІС‚РѕСЂРѕР№ РєР°РЅР°Р»")
                except Exception as e:
                    logger.error(f"РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё РѕС‚С‡РµС‚Р° РІРѕ РІС‚РѕСЂРѕР№ РєР°РЅР°Р»: {e}")

            self.last_hourly_report = now

# ============================================
# РћР‘Р РђР‘РћРўР§РРљР РљРћРњРђРќР”
# ============================================
router = Router()

# Р¤РёР»СЊС‚СЂ РґР»СЏ РїСЂРѕРІРµСЂРєРё ID Р°РґРјРёРЅР°
class IsAdmin(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return str(message.from_user.id) == ADMIN_ID

@router.message(Command("start", "help"), IsAdmin())
async def cmd_help(message: types.Message):
    msg = """
рџ¤– <b>Р‘РѕС‚ РјРѕРЅРёС‚РѕСЂРёРЅРіР° Momentum</b>

<b>Р”РѕСЃС‚СѓРїРЅС‹Рµ РєРѕРјР°РЅРґС‹:</b>
/status - РџРѕРєР°Р·Р°С‚СЊ СЃС‚Р°С‚СѓСЃ Р±РѕС‚Р°
/check - Р—Р°РїСѓСЃС‚РёС‚СЊ РїСЂРѕРІРµСЂРєСѓ РІР°РєР°РЅСЃРёР№ СЃРµР№С‡Р°СЃ
/help - РџРѕРєР°Р·Р°С‚СЊ СЌС‚Рѕ СЃРѕРѕР±С‰РµРЅРёРµ

Р‘РѕС‚ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїСЂРѕРІРµСЂСЏРµС‚ СЃР°Р№С‚ РєР°Р¶РґС‹Рµ 2 РјРёРЅСѓС‚С‹ Рё СѓРІРµРґРѕРјР»СЏРµС‚ РѕР± РѕС‚РєСЂС‹С‚РёРё СЂРµРіРёСЃС‚СЂР°С†РёРё.
"""
    await message.answer(msg, parse_mode=ParseMode.HTML)

# РћР±СЂР°Р±РѕС‚С‡РёРєРё РґР»СЏ РѕР±С‹С‡РЅС‹С… РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ (Momentum Pro)

@router.message(Command("start"))
async def cmd_start_public(message: types.Message, sub_manager: SubscriptionManager):
    """РџСѓР±Р»РёС‡РЅР°СЏ РєРѕРјР°РЅРґР° СЃС‚Р°СЂС‚"""
    # Р•СЃР»Рё Р°РґРјРёРЅ, РјРѕР¶РЅРѕ РїРѕРєР°Р·Р°С‚СЊ Р°РґРјРёРЅ-РїР°РЅРµР»СЊ РёР»Рё РѕСЃС‚Р°РІРёС‚СЊ РєР°Рє РµСЃС‚СЊ
    if str(message.from_user.id) == ADMIN_ID:
        await cmd_help(message)
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="рџљЂ Momentum Pro", callback_data="buy_momentum_pro")
    
    status_text = ""
    if sub_manager.is_active(message.from_user.id):
        expiry = sub_manager.get_expiry(message.from_user.id)
        status_text = f"\n\nвњ… <b>Р’Р°С€Р° РїРѕРґРїРёСЃРєР° Р°РєС‚РёРІРЅР° РґРѕ:</b> {expiry.strftime('%d.%m.%Y %H:%M')}"

    msg = f"""
рџ‘‹ РџСЂРёРІРµС‚СЃС‚РІСѓСЋ РІ Р±РѕС‚Рµ <b>Momentum</b>!

Р—РґРµСЃСЊ РІС‹ РјРѕР¶РµС‚Рµ РїСЂРёРѕР±СЂРµСЃС‚Рё РґРѕСЃС‚СѓРї РІ Р·Р°РєСЂС‹С‚С‹Р№ РєР°РЅР°Р» <b>Momentum Pro</b>.
{status_text}
РќР°Р¶РјРёС‚Рµ РєРЅРѕРїРєСѓ РЅРёР¶Рµ, С‡С‚РѕР±С‹ СѓР·РЅР°С‚СЊ РїРѕРґСЂРѕР±РЅРѕСЃС‚Рё.
"""
    await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.callback_query(F.data == "buy_momentum_pro")
async def process_momentum_pro(callback: types.CallbackQuery):
    """РџРѕРєР°Р· РїРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРѕРіРѕ СЃРѕРіР»Р°С€РµРЅРёСЏ"""
    kb = InlineKeyboardBuilder()
    kb.button(text="рџ’і РљСѓРїРёС‚СЊ РґРѕСЃС‚СѓРї (250 в­ђ)", callback_data="pay_stars")
    kb.button(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="back_to_start")
    kb.adjust(1)

    agreement = """
рџ“њ <b>РџРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРѕРµ СЃРѕРіР»Р°С€РµРЅРёРµ (Momentum Pro)</b>

РџРѕРєСѓРїР°СЏ РґРѕСЃС‚СѓРї, РІС‹ РїРѕРґС‚РІРµСЂР¶РґР°РµС‚Рµ, С‡С‚Рѕ:
1. РћРїР»Р°С‚Р° РїСЂРѕРёР·РІРѕРґРёС‚СЃСЏ РЅР° РґРѕР±СЂРѕРІРѕР»СЊРЅРѕР№ РѕСЃРЅРѕРІРµ.
2. Р”РѕСЃС‚СѓРї РїСЂРµРґРѕСЃС‚Р°РІР»СЏРµС‚СЃСЏ РЅР° 30 РґРЅРµР№.
3. Р’С‹ РѕР·РЅР°РєРѕРјР»РµРЅС‹ СЃ РїСЂР°РІРёР»Р°РјРё РєР°РЅР°Р»Р°.
4. Р’РѕР·РІСЂР°С‚ СЃСЂРµРґСЃС‚РІ Р·Р° С†РёС„СЂРѕРІС‹Рµ С‚РѕРІР°СЂС‹ РЅРµ РїСЂРµРґСѓСЃРјРѕС‚СЂРµРЅ РїРѕР»РёС‚РёРєРѕР№ Telegram.

РЎС‚РѕРёРјРѕСЃС‚СЊ РґРѕСЃС‚СѓРїР°: <b>250 Telegram Stars</b>
РЎСЂРѕРє РґРµР№СЃС‚РІРёСЏ: <b>30 РґРЅРµР№</b>
"""
    await callback.message.edit_text(agreement, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.callback_query(F.data == "back_to_start")
async def process_back_to_start(callback: types.CallbackQuery, sub_manager: SubscriptionManager):
    """Р’РѕР·РІСЂР°С‚ РІ РіР»Р°РІРЅРѕРµ РјРµРЅСЋ"""
    kb = InlineKeyboardBuilder()
    kb.button(text="рџљЂ Momentum Pro", callback_data="buy_momentum_pro")
    
    status_text = ""
    if sub_manager.is_active(callback.from_user.id):
        expiry = sub_manager.get_expiry(callback.from_user.id)
        status_text = f"\n\nвњ… <b>Р’Р°С€Р° РїРѕРґРїРёСЃРєР° Р°РєС‚РёРІРЅР° РґРѕ:</b> {expiry.strftime('%d.%m.%Y %H:%M')}"

    msg = f"""
рџ‘‹ РџСЂРёРІРµС‚СЃС‚РІСѓСЋ РІ Р±РѕС‚Рµ <b>Momentum</b>!

Р—РґРµСЃСЊ РІС‹ РјРѕР¶РµС‚Рµ РїСЂРёРѕР±СЂРµСЃС‚Рё РґРѕСЃС‚СѓРї РІ Р·Р°РєСЂС‹С‚С‹Р№ РєР°РЅР°Р» <b>Momentum Pro</b>.
{status_text}
РќР°Р¶РјРёС‚Рµ РєРЅРѕРїРєСѓ РЅРёР¶Рµ, С‡С‚РѕР±С‹ СѓР·РЅР°С‚СЊ РїРѕРґСЂРѕР±РЅРѕСЃС‚Рё.
"""
    await callback.message.edit_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.callback_query(F.data == "pay_stars")
async def send_payment_invoice(callback: types.CallbackQuery, bot: Bot):
    """РћС‚РїСЂР°РІРєР° СЃС‡РµС‚Р° РЅР° РѕРїР»Р°С‚Сѓ"""
    prices = [LabeledPrice(label="Momentum Pro (30 РґРЅРµР№)", amount=STARS_PRICE)]
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Р”РѕСЃС‚СѓРї РІ Momentum Pro",
        description="РџРѕРґРїРёСЃРєР° РЅР° Р·Р°РєСЂС‹С‚С‹Р№ РєР°РЅР°Р» РЅР° 30 РґРЅРµР№",
        payload="momentum_pro_30_days",
        currency="XTR", # РљРѕРґ РґР»СЏ Telegram Stars
        prices=prices,
        provider_token="" # РџСѓСЃС‚Рѕ РґР»СЏ Telegram Stars
    )
    await callback.answer()

@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """РџРѕРґС‚РІРµСЂР¶РґРµРЅРёРµ РїРµСЂРµРґ РѕРїР»Р°С‚РѕР№"""
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(message: types.Message, sub_manager: SubscriptionManager, bot: Bot):
    """РћР±СЂР°Р±РѕС‚РєР° СѓСЃРїРµС€РЅРѕР№ РѕРїР»Р°С‚С‹"""
    if message.successful_payment.invoice_payload == "momentum_pro_30_days":
        expiry = sub_manager.add_subscription(message.from_user.id)
        
        kb = InlineKeyboardBuilder()
        # РЎСЃС‹Р»РєР° РЅР° РєР°РЅР°Р» (РЅСѓР¶РЅРѕ СЃРѕР·РґР°С‚СЊ invite link РµСЃР»Рё РµРіРѕ РЅРµС‚, РЅРѕ Р·РґРµСЃСЊ РїСЂРµРґРїРѕР»Р°РіР°РµС‚СЃСЏ С‡С‚Рѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РїРѕРґР°СЃС‚ Р·Р°СЏРІРєСѓ)
        # Р’ aiogram 3.x РґР»СЏ РїРѕР»СѓС‡РµРЅРёСЏ СЃСЃС‹Р»РєРё РЅР° Р·Р°РєСЂС‹С‚С‹Р№ РєР°РЅР°Р» РјРѕР¶РЅРѕ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ bot.create_chat_invite_link
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=MOMENTUM_PRO_CHANNEL_ID,
                creates_join_request=True # РћР±СЏР·Р°С‚РµР»СЊРЅРѕ, С‡С‚РѕР±С‹ Р±РѕС‚ РІРёРґРµР» Р·Р°СЏРІРєСѓ
            )
            kb.button(text="вћЎпёЏ РџРѕРґР°С‚СЊ Р·Р°СЏРІРєСѓ РІ РєР°РЅР°Р»", url=invite.invite_link)
        except Exception as e:
            logger.error(f"РћС€РёР±РєР° СЃРѕР·РґР°РЅРёСЏ СЃСЃС‹Р»РєРё: {e}")
            kb.button(text="вћЎпёЏ РџРѕРґР°С‚СЊ Р·Р°СЏРІРєСѓ", url="https://t.me/c/1003836921999/1") # Р—Р°РіР»СѓС€РєР°

        msg = f"""
рџЋ‰ <b>РћРїР»Р°С‚Р° РїСЂРѕС€Р»Р° СѓСЃРїРµС€РЅРѕ!</b>

Р’Р°С€Р° РїРѕРґРїРёСЃРєР° Р°РєС‚РёРІРёСЂРѕРІР°РЅР° РґРѕ: <b>{expiry.strftime('%d.%m.%Y %H:%M')}</b>.
РўРµРїРµСЂСЊ РІС‹ РјРѕР¶РµС‚Рµ РїРѕРґР°С‚СЊ Р·Р°СЏРІРєСѓ РЅР° РІСЃС‚СѓРїР»РµРЅРёРµ РІ РєР°РЅР°Р». Р‘РѕС‚ РѕРґРѕР±СЂРёС‚ РµС‘ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё.
"""
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.chat_join_request(F.chat.id == MOMENTUM_PRO_CHANNEL_ID)
async def handle_join_request(update: ChatJoinRequest, sub_manager: SubscriptionManager):
    """РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРѕРµ РѕРґРѕР±СЂРµРЅРёРµ Р·Р°СЏРІРѕРє"""
    user_id = update.from_user.id
    if sub_manager.is_active(user_id):
        try:
            await update.approve()
            logger.info(f"РћРґРѕР±СЂРµРЅР° Р·Р°СЏРІРєР° РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ {user_id} РІ Momentum Pro")

            # РњРѕР¶РЅРѕ РѕС‚РїСЂР°РІРёС‚СЊ РїСЂРёРІРµС‚СЃС‚РІРµРЅРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ РІ Р›РЎ
            await update.bot.send_message(
                user_id,
                "вњ… Р’Р°С€Р° Р·Р°СЏРІРєР° РІ <b>Momentum Pro</b> РѕРґРѕР±СЂРµРЅР°! Р”РѕР±СЂРѕ РїРѕР¶Р°Р»РѕРІР°С‚СЊ.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"РћС€РёР±РєР° РѕРґРѕР±СЂРµРЅРёСЏ Р·Р°СЏРІРєРё: {e}")
    else:
        # Р•СЃР»Рё РїРѕРґРїРёСЃРєРё РЅРµС‚ вЂ” РѕС‚РєР»РѕРЅСЏРµРј Р·Р°СЏРІРєСѓ
        try:
            await update.decline()
            logger.warning(f"РћС‚РєР»РѕРЅРµРЅР° Р·Р°СЏРІРєР° РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ {user_id} (РЅРµС‚ Р°РєС‚РёРІРЅРѕР№ РїРѕРґРїРёСЃРєРё)")
            
            # РЈРІРµРґРѕРјР»СЏРµРј РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ
            await update.bot.send_message(
                user_id,
                "вќЊ Р’Р°С€Р° Р·Р°СЏРІРєР° РѕС‚РєР»РѕРЅРµРЅР°.\n\n"
                "Р’РѕР·РјРѕР¶РЅРѕ, РІР°С€Р° РїРѕРґРїРёСЃРєР° РёСЃС‚РµРєР»Р°. РџСЂРёРѕР±СЂРµС‚РёС‚Рµ РїРѕРґРїРёСЃРєСѓ Р·Р°РЅРѕРІРѕ С‡РµСЂРµР· Р±РѕС‚Р°.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"РћС€РёР±РєР° РїСЂРё РѕС‚РєР»РѕРЅРµРЅРёРё Р·Р°СЏРІРєРё {user_id}: {e}")

@router.my_chat_member(F.chat.id.in_([TELEGRAM_CHAT_ID, SECONDARY_CHAT_ID]))
async def handle_chat_member_update(event: ChatMemberUpdated, bot: Bot):
    """РЈРґР°Р»РµРЅРёРµ СЃРёСЃС‚РµРјРЅС‹С… СЃРѕРѕР±С‰РµРЅРёР№ Рѕ РІСЃС‚СѓРїР»РµРЅРёРё/РІС‹С…РѕРґРµ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№"""
    try:
        chat_id = event.chat.id
        old_status = event.old_chat_member.status
        new_status = event.new_chat_member.status
        
        # РЈРґР°Р»СЏРµРј СЃРёСЃС‚РµРјРЅС‹Рµ СЃРѕРѕР±С‰РµРЅРёСЏ Рѕ РІСЃС‚СѓРїР»РµРЅРёРё, РІС‹С…РѕРґРµ, РїСЂРёРіР»Р°С€РµРЅРёРё
        if old_status == ChatMemberStatus.LEFT and new_status == ChatMemberStatus.MEMBER:
            # РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РІСЃС‚СѓРїРёР»
            await bot.delete_message(chat_id, event.message.message_id)
            logger.info(f"РЈРґР°Р»РµРЅРѕ СЃРёСЃС‚РµРјРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ Рѕ РІСЃС‚СѓРїР»РµРЅРёРё РІ С‡Р°С‚Рµ {chat_id}")
        elif old_status == ChatMemberStatus.MEMBER and new_status == ChatMemberStatus.LEFT:
            # РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РІС‹С€РµР»
            await bot.delete_message(chat_id, event.message.message_id)
            logger.info(f"РЈРґР°Р»РµРЅРѕ СЃРёСЃС‚РµРјРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ Рѕ РІС‹С…РѕРґРµ РёР· С‡Р°С‚Р° {chat_id}")
        elif old_status == ChatMemberStatus.RESTRICTED and new_status == ChatMemberStatus.MEMBER:
            # РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р±С‹Р» СЂР°Р·Р±Р»РѕРєРёСЂРѕРІР°РЅ/РІРѕР·РІСЂР°С‰РµРЅ
            await bot.delete_message(chat_id, event.message.message_id)
            logger.info(f"РЈРґР°Р»РµРЅРѕ СЃРёСЃС‚РµРјРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ Рѕ РІРѕР·РІСЂР°С‰РµРЅРёРё РІ С‡Р°С‚ {chat_id}")
    except Exception as e:
        # РРіРЅРѕСЂРёСЂСѓРµРј РѕС€РёР±РєРё (РЅР°РїСЂРёРјРµСЂ, РµСЃР»Рё СЃРѕРѕР±С‰РµРЅРёРµ СѓР¶Рµ СѓРґР°Р»РµРЅРѕ РёР»Рё РЅРµС‚ РїСЂР°РІ)
        logger.debug(f"РќРµ СѓРґР°Р»РѕСЃСЊ СѓРґР°Р»РёС‚СЊ СЃРёСЃС‚РµРјРЅРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ: {e}")

@router.message(Command("status"), IsAdmin())
async def cmd_status(message: types.Message, monitor: VacancyMonitor):
    await message.answer(monitor.get_status_message(), parse_mode=ParseMode.HTML)

@router.message(Command("check"), IsAdmin())
async def cmd_check(message: types.Message, monitor: VacancyMonitor):
    await message.answer("рџ”„ <b>Р—Р°РїСѓСЃРєР°СЋ РІРЅРµРїР»Р°РЅРѕРІСѓСЋ РїСЂРѕРІРµСЂРєСѓ...</b>", parse_mode=ParseMode.HTML)
    
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=HTTP_TIMEOUT, sock_read=HTTP_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        snapshot = await monitor.collect_snapshot(session)
    
    if snapshot["vacancies"] and snapshot["healthy"]:
        current = snapshot["vacancies"]
        total = len(current)
        suspended = sum(1 for v in current if monitor.status_is_suspended(v['status']))
        active = total - suspended

        monitor.last_snapshot_meta = {
            "total_vacancies": snapshot["total_vacancies"],
            "pages_checked": snapshot["pages_checked"],
            "pages_succeeded": snapshot["pages_succeeded"],
            "pages_failed": snapshot["pages_failed"],
            "parse_errors": snapshot["parse_errors"],
            "collected_at": snapshot["collected_at"],
        }
        await monitor.analyze_changes(current)
        
        await message.answer(f"вњ… <b>РџСЂРѕРІРµСЂРєР° Р·Р°РІРµСЂС€РµРЅР°!</b>\n\nР’СЃРµРіРѕ: {total}\nрџ”ґ РџСЂРёРѕСЃС‚Р°РЅРѕРІР»РµРЅРѕ: {suspended}\nрџџў РђРєС‚РёРІРЅС‹С…: {active}", parse_mode=ParseMode.HTML)
    elif snapshot["vacancies"]:
        await message.answer(
            "вљ пёЏ <b>РџСЂРѕРІРµСЂРєР° Р·Р°РІРµСЂС€РёР»Р°СЃСЊ, РЅРѕ СЃРЅРёРјРѕРє СЃР°Р№С‚Р° РІС‹РіР»СЏРґРёС‚ РЅРµРїРѕР»РЅС‹Рј.</b>\n\n"
            f"Р’Р°РєР°РЅСЃРёР№ СЃРѕР±СЂР°РЅРѕ: {snapshot['total_vacancies']}\n"
            f"РЎС‚СЂР°РЅРёС† СѓСЃРїРµС€РЅРѕ: {snapshot['pages_succeeded']}\n"
            f"РЎС‚СЂР°РЅРёС† СЃ РѕС€РёР±РєРѕР№: {snapshot['pages_failed']}\n"
            f"РћС€РёР±РѕРє РїР°СЂСЃРёРЅРіР°: {snapshot['parse_errors']}",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("вљ пёЏ РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ РґР°РЅРЅС‹Рµ СЃ СЃР°Р№С‚Р°", parse_mode=ParseMode.HTML)

# ============================================
# Р—РђРџРЈРЎРљ
# ============================================

async def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("вќЊ РћРЁРР‘РљРђ: РџСЂРѕРІРµСЂСЊС‚Рµ .env С„Р°Р№Р» РЅР° РЅР°Р»РёС‡РёРµ TELEGRAM_BOT_TOKEN Рё TELEGRAM_CHAT_ID")
        return

    # РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ Р±РѕС‚Р°
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    
    # РЎРѕР·РґР°РµРј РјРѕРЅРёС‚РѕСЂ Рё РјРµРЅРµРґР¶РµСЂ РїРѕРґРїРёСЃРѕРє
    monitor = VacancyMonitor(bot)
    sub_manager = SubscriptionManager()
    
    dp["monitor"] = monitor
    dp["sub_manager"] = sub_manager
    
    dp.include_router(router)
    

    logger.info("Р‘РѕС‚ Р·Р°РїСѓС‰РµРЅ!")
    
    # Р—Р°РїСѓСЃРє РјРѕРЅРёС‚РѕСЂР° Рё Р±РѕС‚Р° РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ
    await asyncio.gather(
        dp.start_polling(bot),
        monitor.monitor_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("РћСЃС‚Р°РЅРѕРІР»РµРЅРѕ.")

