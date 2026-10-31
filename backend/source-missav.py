import time
import datetime
import re
import threading
from urllib.parse import urlparse
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

def parse_release_date(date_str):
    if not date_str:
        return ""
    date_str = date_str.strip().lower()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return f"{date_str} 00:00:00"
    return date_str

class Scraper:
    def __init__(self, db_conn, db_lock, memory_lock, db_buffer, table_name="javtiful_videos", domain=None):
        self.db_conn = db_conn
        self.db_lock = db_lock
        self.memory_lock = memory_lock
        self.db_buffer = db_buffer
        self.table_name = table_name
        self.domain = domain if domain else "missav.ai"
        self.session = curl_requests.Session(impersonate="chrome120")
        self.sync_lock = threading.Lock()
        self.referer = f"https://{self.domain}/"
        self.source_name = "MissAV"

    def update_sync_tasks_from_menu(self):
        custom_log(self.source_name, f"Khởi tạo sync_tasks cho {self.source_name}...")
        tasks = [
            f"https://{self.domain}/en/new?page={{page}}",
            f"https://{self.domain}/en/uncensored-leak?page={{page}}",
            f"https://{self.domain}/en/genres/VR?page={{page}}",
            f"https://{self.domain}/en/chinese-subtitle?page={{page}}"
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
        custom_log(self.source_name, f"⏳ Syncing {url}")
        try:
            res = self.session.get(url, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            videos = []
            
            total_pages = 2000
            last_page_link = soup.select('a[href*="?page="]')
            for a in last_page_link:
                try:
                    num = int(a.text.strip())
                    if num > total_pages:
                        total_pages = num
                except:
                    pass
            
            items = soup.select('div.thumbnail, div.max-w-64, div[class*="aspect-video"]') 
            if not items:
                items = soup.select(f'a[href*="{self.domain}/en/"]')
                
            now = int(time.time())
            for idx, item in enumerate(items):
                a_tag = item if item.name == 'a' else item.select_one('a')
                if not a_tag: continue
                href = a_tag.get('href', '')
                
                if '/genres/' in href or '/makers/' in href or '/actresses/' in href or '?page=' in href: continue
                
                parts = href.split('/')
                vid_id = parts[-1]
                if not vid_id: continue
                
                img_tag = a_tag.select_one('img')
                if not img_tag and item.name != 'a':
                    img_tag = item.select_one('img')
                    
                cover = img_tag.get('data-src') or img_tag.get('src') if img_tag else ''
                title_tag = item.select_one('.text-secondary') or item.select_one('a.truncate') or a_tag
                title = title_tag.text.strip() if title_tag else vid_id
                dvd = title.split(' ')[0] if title else ''
                
                if len(title) < 4: continue
                
                if vid_id and cover and self.domain.split('.')[0] in href:
                    pseudo_time = now - (page * 10000) - idx
                    added_at_dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(pseudo_time))
                    videos.append((vid_id, title, cover, added_at_dt, "", dvd))
                    
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
        dummy_uuid = "bc21a4fe-5e9b-4936-a844-b3e5f04c4cdc"
        if not force_refresh:
            with self.memory_lock:
                url_in_buffer = self.db_buffer['video_urls'].get(vid_id)
            if url_in_buffer and dummy_uuid not in url_in_buffer:
                url_str = url_in_buffer
                parsed_domain = urlparse(url_str).netloc
                if not re.match(r'^[a-f0-9]{8}\.com$', parsed_domain):
                    return url_str
                    
            with self.db_lock:
                cursor = self.db_conn.cursor()
                cursor.execute(f"SELECT url, surrit_id FROM {self.table_name} WHERE (id = ? OR upper(dvd) = ?) AND (url IS NOT NULL OR surrit_id IS NOT NULL)", (vid_id, vid_id.upper()))
                row = cursor.fetchone()
                if not row:
                    try:
                        cursor.execute("SELECT playlist_url, surrit_id FROM streams WHERE lower(missav_id) = ? OR upper(code) = ? LIMIT 1", (vid_id.lower(), vid_id.upper()))
                        row = cursor.fetchone()
                    except Exception:
                        pass
            
            if row:
                sid = row[1] if (row[1] and row[1] != dummy_uuid and row[1] != 'failed') else None
                url_str = row[0] if (row[0] and dummy_uuid not in row[0] and 'failed' not in row[0]) else None
                if not url_str and sid:
                    url_str = f"https://surrit.com/{sid}/playlist.m3u8"
                if url_str:
                    url_str = url_str.replace('1080p/video.m3u8', 'playlist.m3u8')
                    parsed_domain = urlparse(url_str).netloc
                    # Phát hiện và bỏ qua tên miền lỗi do thuật toán cũ
                    if not re.match(r'^[a-f0-9]{8}\.com$', parsed_domain):
                        return url_str
            
        custom_log(self.source_name, f"⏳ Fetching video URL for {vid_id}")
        
        # Danh sách domain mirror MissAV ưu tiên thử
        candidate_domains = [self.domain, "missav99.com", "missav123.com", "missav.ws", "missav.ai"]
        seen_domains = set()
        ordered_domains = []
        for d in candidate_domains:
            if d and d not in seen_domains:
                seen_domains.add(d)
                ordered_domains.append(d)

        # Danh sách path thử: vid_id gốc và biến thể uncensored-leak
        candidate_slugs = [vid_id]
        if not vid_id.endswith('-uncensored-leak'):
            candidate_slugs.append(f"{vid_id}-uncensored-leak")

        for d in ordered_domains:
            for slug in candidate_slugs:
                url = f"https://{d}/en/{slug}"
                try:
                    res = self.session.get(url, timeout=12, headers={"Referer": f"https://{d}/"})
                    if res.status_code != 200:
                        continue
                    html_text = res.text

                    # 1. Kiểm tra mã hóa Hex trong các mirror như missav99 (document.write(unescape(func("hex"))))
                    idx_hex = html_text.find('("')
                    if idx_hex != -1:
                        next_q = html_text.find('"', idx_hex + 2)
                        if next_q != -1 and (next_q - (idx_hex + 2)) > 1000:
                            hex_str = html_text[idx_hex + 2:next_q]
                            try:
                                decoded_html = bytes.fromhex(hex_str).decode('utf-8', errors='ignore')
                                if decoded_html:
                                    html_text = decoded_html
                            except Exception:
                                pass

                    # 2. Tìm m3u8 trong window.videoConfig = { ... url: "..." }
                    cfg_match = re.search(r'window\.videoConfig\s*=\s*\{[^}]*?url:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html_text)
                    if cfg_match:
                        m3u8_url = cfg_match.group(1).replace('\\/', '/')
                        with self.memory_lock:
                            self.db_buffer['video_urls'][vid_id] = m3u8_url
                        return m3u8_url

                    # 3. Tìm m3u8 qua eval(function(p,a,c,k,e,d)...)
                    m3u8_url = None
                    eval_match = re.search(r"return p}\('(.*?)',\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\.split\('\|'\)", html_text)
                    if eval_match:
                        p_str = eval_match.group(1).replace("\\'", "'")
                        a_radix = int(eval_match.group(2))
                        c_count = int(eval_match.group(3))
                        k_words = eval_match.group(4).split('|')
                        
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
                        
                        playlist_match = re.search(r'(https://[^/\'"]+/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/playlist\.m3u8)', p_str)
                        if playlist_match:
                            m3u8_url = playlist_match.group(1)
                        else:
                            any_m3u8 = re.search(r'(https://[^/\'"]+/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/[^/]+/[^\'"]*\.m3u8)', p_str)
                            if any_m3u8:
                                m3u8_url = any_m3u8.group(1)
                                m3u8_url = re.sub(r'/[^/]+/[^\']*\.m3u8$', '/playlist.m3u8', m3u8_url)

                    # 4. Tìm UUID trực tiếp nếu có
                    if not m3u8_url:
                        uuids_found = set(re.findall(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', html_text))
                        uuids_found.discard(dummy_uuid)
                        if uuids_found:
                            video_uuid = list(uuids_found)[0]
                            m3u8_url = f"https://surrit.com/{video_uuid}/playlist.m3u8"

                    if m3u8_url:
                        with self.memory_lock:
                            self.db_buffer['video_urls'][vid_id] = m3u8_url
                        return m3u8_url

                except Exception as e:
                    custom_log(self.source_name, f"⚠️ Lỗi fetch {url}: {e}")

        custom_log(self.source_name, f"⚠️ Không tìm thấy URL video cho {vid_id}")
        return None

    def sync_video_details(self, vid_id):
        with self.sync_lock:
            url = f"https://{self.domain}/en/{vid_id}"
            try:
                res = self.session.get(url, timeout=15)
                if res.status_code != 200:
                    with self.db_lock:
                        cursor = self.db_conn.cursor()
                        cursor.execute(f"UPDATE {self.table_name} SET details_fetched = -1 WHERE id = ?", (vid_id,))
                        self.db_conn.commit()
                    return False
                soup = BeautifulSoup(res.text, 'html.parser')
                
                actress_arr = [a.text.strip() for a in soup.select('a[href*="/actresses/"]') if a.text.strip()]
                genre_arr = [a.text.strip() for a in soup.select('a[href*="/genres/"]') if a.text.strip()]
                
                maker_tag = soup.select_one('a[href*="/makers/"]')
                maker = maker_tag.text.strip() if maker_tag else ""
                
                date_match = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', res.text)
                release_date = date_match.group(1) + " 00:00:00" if date_match else ""
                
                desc_meta = soup.find('meta', {'name': 'description'})
                details = desc_meta.get('content', '') if desc_meta else ""

                # Bóc tách UUID / surrit_id và m3u8 playlist để play mượt trên Termux
                m3u8_url = None
                surrit_id = None
                uuid_match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', res.text)
                if uuid_match:
                    surrit_id = uuid_match.group(1)
                    m3u8_url = f"https://surrit.com/{surrit_id}/playlist.m3u8"
                
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    # Đảm bảo có cột surrit_id
                    try:
                        cursor.execute(f"ALTER TABLE {self.table_name} ADD COLUMN surrit_id TEXT")
                    except sqlite3.OperationalError:
                        pass

                    if m3u8_url:
                        cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, maker = ?, details = ?, release_date = ?, url = ?, surrit_id = ?, details_fetched = 1 WHERE id = ?''', (", ".join(set(actress_arr)), ", ".join(set(genre_arr)), maker, details, release_date or "", m3u8_url, surrit_id, vid_id))
                    elif release_date:
                        cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, maker = ?, details = ?, release_date = ?, details_fetched = 1 WHERE id = ?''', (", ".join(set(actress_arr)), ", ".join(set(genre_arr)), maker, details, release_date, vid_id))
                    else:
                        cursor.execute(f'''UPDATE {self.table_name} SET actress = ?, genre = ?, maker = ?, details = ?, details_fetched = 1 WHERE id = ?''', (", ".join(set(actress_arr)), ", ".join(set(genre_arr)), maker, details, vid_id))
                    self.db_conn.commit()
                custom_log(self.source_name, f"{self.source_name} {vid_id} [Surrit: {surrit_id or 'None'}] {len(actress_arr)} actress{'es' if len(actress_arr) != 1 else ''}, {len(genre_arr)} genre{'s' if len(genre_arr) != 1 else ''}, {maker}")
                return True
            except Exception as e:
                custom_log(self.source_name, f"❌ Lỗi lấy chi tiết video {vid_id}: {e}")
                with self.db_lock:
                    cursor = self.db_conn.cursor()
                    cursor.execute(f"UPDATE {self.table_name} SET details_fetched = -1 WHERE id = ?", (vid_id,))
                    self.db_conn.commit()
                return False