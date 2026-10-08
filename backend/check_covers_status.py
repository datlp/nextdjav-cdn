import sqlite3
import os

sources = {
    'vlxx': (r'D:\Dat\Database\vlxx\vlxx.db', 'vlxx_videos', r'D:\Dat\Database\vlxx\vlxx_covers_0001.bin'),
    'javtiful': (r'D:\Dat\Database\javtiful\javtiful.db', 'javtiful_videos', r'D:\Dat\Database\javtiful\javtiful_covers_0001.bin'),
    'missav': (r'D:\Dat\Database\termux-services\missav.db', 'missav_videos', r'D:\Dat\Database\termux-services\missav\missav_covers_0001.bin')
}

for name, (db_path, table, bin_path) in sources.items():
    if not os.path.exists(db_path):
        print(f"{name}: db not found at {db_path}")
        continue
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    total = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE cover_bin_id IS NOT NULL AND cover_length > 0")
    in_bin = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE cover IS NOT NULL AND cover != '' AND (cover_length IS NULL OR cover_length = 0)")
    missing = cur.fetchone()[0]
    
    bin_size = os.path.getsize(bin_path) if os.path.exists(bin_path) else 0
    print(f"{name} ({table}): total={total}, in_bin={in_bin}, missing={missing}, bin_size={bin_size / (1024*1024):.2f} MB")
    conn.close()
