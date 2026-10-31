import time
import datetime
import re
import json
import threading
import asyncio
import sys
import subprocess
import builtins
from urllib.parse import urlparse
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

# Đảm bảo custom_log luôn hoạt động kể cả khi chạy test độc lập
if not hasattr(builtins, 'custom_log'):
    def custom_log(category, msg):
        print(f"[{category}] {msg}")
    builtins.custom_log = custom_log

# Tự động cài đặt Selenium nếu hệ thống chưa có
try:
    from selenium import webdriver
except ImportError:
    custom_log("System", "Đang cài đặt thư viện Selenium để vượt rào Cloudflare cho JavGuru...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "selenium"])
    from selenium import webdriver

def parse_release_date(date_str):
    if not date_str:
        return ""
    date_str = date_str.strip().lower()
    now = datetime.datetime.now()
    
    if re.match(r'^\d{1,2}:\d{2}:\d{2}$', date_str) or re.match(r'^\d{1,2}:\d{2}$', date_str):
        return ""
    if re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', date_str):
        return date_str
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return f"{date_str} 00:00:00"
        
    match_en = re.match(r'^(\d+)\s+(year|month|week|day|hour|minute|second)s?\s+ago$', date_str)
    match_vi = re.match(r'^(\d+)\s+(năm|tháng|tuần|ngày|giờ|phút|giây)\s+trước$', date_str)
    
    if match_en or match_vi:
        if match_en:
            val, unit = int(match_en.group(1)), match_en.group(2)
        else:
            val = int(match_vi.group(1))
            unit_map = {'năm': 'year', 'tháng': 'month', 'tuần': 'week', 'ngày': 'day', 'giờ': 'hour', 'phút': 'minute', 'giây': 'second'}
            unit = unit_map[match_vi.group(2)]
            
        if unit == 'year': dt = now - datetime.timedelta(days=val*365)
        elif unit == 'month': dt = now - datetime.timedelta(days=val*30)
        elif unit == 'week': dt = now - datetime.timedelta(weeks=val)
        elif unit == 'day': dt = now - datetime.timedelta(days=val)
        elif unit == 'hour': dt = now - datetime.timedelta(hours=val)
        elif unit == 'minute': dt = now - datetime.timedelta(minutes=val)
        elif unit == 'second': dt = now - datetime.timedelta(seconds=val)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
        
    if re.match(r'^\d{2}-\d{2}-\d{2}$', date_str):
        parts = date_str.split('-')
        year = int(parts[0])
        if year < 100: year += 2000
        return f"{year}-{parts[1]}-{parts[2]} 00:00:00"

    return date_str

class Scraper:
    def __init__(self, db_conn, db_lock, memory_lock, db_buffer, table_name="javguru_videos", domain=None):
        self.db_conn = db_conn
        self.db_lock = db_lock
        self.memory_lock = memory_lock
        self.db_buffer = db_buffer
        self.table_name = table_name
        self.domain = domain if domain else "jav.guru"
        self.session = curl_requests.Session(impersonate="chrome120")
        self.sync_lock = threading.Lock()
        self.referer = f"https://{self.domain}/"
        self.source_name = "JavGuru"
        
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        self.cf_cookie_string = ""
        
        with self.db_lock:
            try:
                self.db_conn.execute('''
                    CREATE TABLE IF NOT EXISTS configs (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                self.db_conn.execute('''
                    CREATE TABLE IF NOT EXISTS play_configs (
                        video_id TEXT PRIMARY KEY,
                        jwplayer_key TEXT,
                        server TEXT,
                        extra_data TEXT
                    )
                ''')
                self.db_conn.commit()
            except:
                pass
                
        threading.Thread(target=self.warm_up, daemon=True).start()

    def load_session(self):
        with self.db_lock:
            cursor = self.db_conn.cursor()
            try:
                cursor.execute("SELECT value FROM configs WHERE key = ?", (f"{self.table_name}_cookies",))
                row = cursor.fetchone()
                if row and row[0]:
                    cookies = json.loads(row[0])
                    cookie_pairs = []
                    for k, v in cookies.items():
                        self.session.cookies.set(k, v, domain=f".{self.domain.replace('www.', '')}")
                        cookie_pairs.append(f"{k}={v}")
                    self.cf_cookie_string = "; ".join(cookie_pairs)
                    custom_log(self.source_name, f"Đã load session từ database.")
            except Exception:
                pass

    def save_session(self):
        cookies = self.session.cookies.get_dict()
        with self.db_lock:
            cursor = self.db_conn.cursor()
            try:
                cursor.execute("INSERT OR REPLACE INTO configs (key, value) VALUES (?, ?)", (f"{self.table_name}_cookies", json.dumps(cookies)))
                self.db_conn.commit()
            except Exception:
                pass

    def bypass_cloudflare(self, target_url):
        custom_log(self.source_name, f"Đang kích hoạt Vệ sĩ Selenium vượt Cloudflare (Native Termux)...")
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            import os
            
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument(f'user-agent={self.user_agent}')
            options.add_argument('--window-size=1280,720')
            
            if "TERMUX_VERSION" in os.environ:
                options.binary_location = '/data/data/com.termux/files/usr/bin/chromium-browser'
                service = Service('/data/data/com.termux/files/usr/bin/chromedriver')
                try:
                    driver = webdriver.Chrome(service=service, options=options)
                except Exception as e:
                    custom_log("System", "❌ Lỗi khởi tạo Chromium trên Termux. Vui lòng chạy lệnh: pkg install x11-repo && pkg install chromium")
                    raise e
            else:
                driver = webdriver.Chrome(options=options)
                
            driver.get(target_url)
            time.sleep(5) 
            
            cookies = driver.get_cookies()
            cookie_pairs = []
            for c in cookies:
                self.session.cookies.set(c['name'], c['value'], domain=c['domain'] if 'domain' in c else f".{self.domain.replace('www.', '')}")
                cookie_pairs.append(f"{c['name']}={c['value']}")
                
            self.cf_cookie_string = "; ".join(cookie_pairs)
            self.save_session()
            
            html_content = driver.page_source
            driver.quit()
            
            custom_log(self.source_name, f"✔️ Cập nhật Token Cloudflare thành công!")
            return html_content
        except Exception as e:
            custom_log(self.source_name, f"❌ Lỗi bypass Cloudflare: {e}")
            return None

    def warm_up(self):
        self.load_session()
        fail_count = 0
        while True:
            try:
                custom_log(self.source_name, "Warming up session... ⏳")
                res = self.session.get(f"https://{self.domain}/", timeout=15)
                if res.status_code in [200, 301, 302, 404] and "Just a moment..." not in res.text:
                    custom_log(self.source_name, "Session is warm. ✔️")
                    fail_count = 0
                    self.save_session()
                else:
                    custom_log(self.source_name, f"Session warm-up requires CF Bypass ⚠️")
                    self.bypass_cloudflare(f"https://{self.domain}/")
            except Exception as e:
                custom_log(self.source_name, f"Session warm-up failed: {e} ❌")
                fail_count += 1
                
            if fail_count >= 3:
                custom_log(self.source_name, "Re-initializing session... 🔄")
                self.session = curl_requests.Session(impersonate="chrome120")
                fail_count = 0
                
            time.sleep(180)

    def update_sync_tasks_from_menu(self):
        custom_log(self.source_name, f"Khởi tạo sync_tasks cho {self.source_name}...")
        tasks = [
            f"https://{self.domain}/page/{{page}}/",
            f"https://{self.domain}/category/jav/page/{{page}}/",
            f"https://{self.domain}/category/english-subbed/page/{{page}}/",
            f"https://{self.domain}/category/decensored/page/{{page}}/",
            f"https://{self.domain}/category/4k/page/{{page}}/"
        ]
        with self.db_lock:
            cursor = self.db_conn.cursor()
            for url in tasks:
                cursor.execute("INSERT OR IGNORE INTO sync_tasks (url_pattern) VALUES (?)", (url,))
            self.db_conn.commit()

    def sync_list_page(self, url_pattern, page):
        with self.sync_lock:
            return self._sync_list_page(url_pattern, page)
            
    def _sync_list_page(self, url_pattern, page):
        url = url_pattern.replace("{page}", str(page))
        if page == 1 and "/page/1" in url:
            url = url.replace("/page/1", "")
            
        custom_log(self.source_name, f"⏳ Syncing {url}")
        try:
            res = self.session.get(url, timeout=15)
            html_text = res.text
            
            if res.status_code in [403, 503] or "Just a moment..." in html_text:
                html_text = self.bypass_cloudflare(url)
                if not html_text:
                    return 0, -1, 0
                    
            soup = BeautifulSoup(html_text, 'html.parser')
            videos = []
            total_pages = 2000
            
            nav_links = soup.select('.wp-pagenavi a.page')
            for a in nav_links:
                try:
                    num = int(a.text.replace(',', '').strip())
                    if num > total_pages or total_pages == 2000:
                        total_pages = num
                except: pass
                
            items = soup.select('div.inside-article')
            now = int(time.time())
            for idx, item in enumerate(items):
                a_tag = item.select_one('div.imgg a')
                if not a_tag: continue
                
                href = a_tag.get('href', '')
                if not href.startswith('http'): continue
                
                vid_id = href.replace(f"https://{self.domain}/", "").strip('/')
                if not vid_id: continue
                
                title_tag = item.select_one('div.grid1 h2 a')
                title = title_tag.text.strip() if title_tag else vid_id
                
                dvd_match = re.search(r'\[(.*?)\]', title)
                dvd = dvd_match.group(1) if dvd_match else (title.split(' ')[0] if title else '')
                
                img_tag = item.select_one('div.imgg img')
                cover = img_tag.get('data-src') or img_tag.get('src') if img_tag else ''
                
                date_tag = item.select_one('div.date')
                release_date = date_tag.text.strip() if date_tag else ""
                release_date = parse_release_date(release_date)
                
                if vid_id and cover:
                    pseudo_time = now - (page * 10000) - idx
                    added_at_dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(pseudo_time))
                    videos.append((vid_id, title, cover, added_at_dt, release_date, dvd))
                    
            with self.db_lock:
                cursor = self.db_conn.cursor()
                new_count = 0
                for vid in set(videos):
                    vid_id = vid[0]
                    cursor.execute(f"SELECT id FROM {self.table_name} WHERE id = ?", (vid_id,))
                    if not cursor.fetchone():
                        with self.memory_lock:
                            if vid_id not in self.db_buffer['videos']:
                                new_count += 1
                                
                with self.memory_lock:
                    for vid in set(videos):
                        vid_id = vid[0]
                        self.db_buffer['videos'][vid_id] = {
                            'id': vid[0], 'title': vid[1], 'cover': vid[2], 'added_at': vid[3], 'release_date': vid[4], 'dvd': vid[5]
                        }
            custom_log(self.source_name, f"{self.source_name} {page} {len(videos)} video{'s' if len(videos) != 1 else ''}")
            return new_count, len(videos), total_pages
        except Exception as e:
            custom_log(self.source_name, f"❌ Sync failed: {e}")
            return 0, -1, 0

    def get_video_url(self, vid_id, force_refresh=False):
        if not force_refresh:
            with self.memory_lock:
                url_in_buffer = self.db_buffer['video_urls'].get(vid_id)
            if url_in_buffer: return url_in_buffer
            
            with self.db_lock:
                cursor = self.db_conn.cursor()
                cursor.execute(f"SELECT url FROM {self.table_name} WHERE id = ? AND url IS NOT NULL", (vid_id,))
                row = cursor.fetchone()
                if row and row[0]: return row[0]

        target_url = f"https://{self.domain}/{vid_id}/"
        if not target_url.endswith('/'): target_url += "/"
        
        custom_log(self.source_name, f"⏳ Fetching video URL for {vid_id}")
        
        html_text = ""
        res = self.session.get(target_url, timeout=15)
        if res.status_code == 200:
            html_text = res.text
        else:
            html_text = self.bypass_cloudflare(target_url)
            
        if not html_text:
            custom_log(self.source_name, "❌ Bị chặn bởi Cloudflare, không lấy được mã nguồn.")
            return None
            
        soup = BeautifulSoup(html_text, 'html.parser')
        iframes = soup.select('iframe')
        
        for iframe in iframes:
            src = iframe.get('src')
            if not src: continue
            if src.startswith('//'): src = 'https:' + src
            
            try:
                iframe_res = self.session.get(src, headers={"Referer": target_url}, timeout=15)
                
                # 1. Tìm trực tiếp m3u8 trong source iframe
                match = re.search(r'["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', iframe_res.text)
                if match:
                    m3u8_url = match.group(1)
                    with self.memory_lock: self.db_buffer['video_urls'][vid_id] = m3u8_url
                    return m3u8_url
                
                # 2. Giải nén code Packed (P.A.C.K.E.R)
                eval_match = re.search(r"return p}\('(.*?)',\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\.split\('\|'\)", iframe_res.text)
                if eval_match:
                    p_str, a_radix_str, c_count_str, k_words_str = eval_match.groups()
                    p_str = p_str.replace("\\'", "'")
                    a_radix = int(a_radix_str)
                    c_count = int(c_count_str)
                    k_words = k_words_str.split('|')
                    
                    def e_base(num, b):
                        if num == 0: return "0"
                        chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        res_str = ""
                        while num > 0:
                            res_str = chars[num % b] + res_str
                            num //= b
                        return res_str
                    
                    for i in range(c_count - 1, -1, -1):
                        if i < len(k_words) and k_words[i]:
                            word = e_base(i, a_radix)
                            p_str = re.sub(r'\b' + word + r'\b', k_words[i], p_str)
                            
                    packed_match = re.search(r'["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', p_str)
                    if packed_match:
                        m3u8_url = packed_match.group(1)
                        with self.memory_lock: self.db_buffer['video_urls'][vid_id] = m3u8_url
                        return m3u8_url
            except Exception as e:
                pass
            
        custom_log(self.source_name, f"⚠️ Không tìm thấy link luồng (m3u8/mp4) trong Iframe.")
        return None

    def sync_video_details(self, vid_id):
        with self.sync_lock:
            target_url = f"https://{self.domain}/{vid_id}/"
            if not target_url.endswith('/'): target_url += "/"
            try:
                res = self.session.get(target_url, timeout=15)
                html_text = res.text
                if res.status_code in [403, 503] or "Just a moment..." in html_text:
                    html_text = self.bypass_cloudflare(target_url)
                    if not html_text:
                        raise Exception("Bị Cloudflare chặn")
                        
                soup = BeautifulSoup(html_text, 'html.parser')
                
                actress_arr = [a.text.strip() for a in soup.select('a[href*="/actress/"]') if a.text.strip()]
                genre_arr = [a.text.strip() for a in soup.select('a[href*="/tag/"]') if a.text.strip()]
                
                maker_tag = soup.select_one('a[href*="/studio/"]') or soup.select_one('a[href*="/maker/"]')
                maker = maker_tag.text.strip() if maker_tag else ""
                
                details = ""
                
                actress_str = ", ".join(set(actress_arr))
                genre_str = ", ".join(set(genre_arr))
                
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, maker = ?, details = ?, details_fetched = 1 WHERE id = ?''', 
                                   (actress_str, genre_str, maker, details, vid_id))
                    self.db_conn.commit()
                    
                custom_log(self.source_name, f"✔️ Bóc tách xong {vid_id}: [💃 {len(actress_arr)} actress | 🎭 {len(genre_arr)} genre]")
                return True
            except Exception as e:
                custom_log(self.source_name, f"❌ Lỗi lấy chi tiết video {vid_id}: {e}")
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    cursor.execute(f"UPDATE {self.table_name} SET details_fetched = -1 WHERE id = ?", (vid_id,))
                    self.db_conn.commit()
                return False

    def clean_keywords(self, text):
        if not text:
            return []
        text = re.sub(r'[^\w\s]', ' ', text.lower())
        words = text.split()
        
        try:
            import nltk
            try:
                nltk.data.find('corpora/stopwords')
            except LookupError:
                nltk.download('stopwords', quiet=True)
            from nltk.corpus import stopwords
            stop_words = set(stopwords.words('english'))
            try:
                stop_words.update(stopwords.words('vietnamese'))
            except Exception:
                pass
        except ImportError:
            stop_words = {'the', 'is', 'are', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'this', 'that'}

        vi_stopwords = {'và', 'của', 'các', 'có', 'được', 'cho', 'trong', 'đã', 'một', 'với', 'những', 'là', 'như', 'hay', 'đang', 'nhưng', 'tại', 'để', 'từ', 'khi', 'làm', 'đến', 'sự', 'này', 'ra', 'phải', 'người', 'về', 'sau', 'rằng', 'chỉ', 'cũng', 'nhiều', 'việc', 'hơn', 'mới', 'vì', 'nếu', 'lại', 'rất', 'còn', 'bởi', 'thì', 'lên', 'đi', 'nào', 'sẽ', 'đó', 'thể', 'theo', 'mình', 'qua', 'phim', 'sex', 'jav', 'vietsub', 'không', 'che', 'hd', 'vlxx', 'full', 'bản', 'đẹp', 'nhất'}
        stop_words.update(vi_stopwords)
        
        words = [w for w in words if w not in stop_words and not w.isdigit() and len(w) > 2]
        return list(dict.fromkeys(words))