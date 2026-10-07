#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def load_simple_env(env_path):
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v

load_simple_env(os.path.join(BASE_DIR, ".env"))

def main():
    port = os.environ.get("PORT", "3013")
    source = os.environ.get("SOURCE", "missav")
    
    db_path = os.environ.get("WINDOWS_DB") if sys.platform == "win32" else os.environ.get("TERMUX_DB")
    nextdjav_db = os.environ.get("WINDOWS_NEXTDJAV_DB") if sys.platform == "win32" else os.environ.get("TERMUX_NEXTDJAV_DB")
    
    cmd = [
        sys.executable,
        os.path.join(BASE_DIR, "backend", "server.py"),
        "-source", source,
        "-port", str(port),
    ]
    if db_path:
        cmd.extend(["-sqlite3", db_path])
    if nextdjav_db:
        cmd.extend(["-nextdjav-db", nextdjav_db])
        
    cmd.extend(sys.argv[1:])
    print(f"🚀 [nextdjav-cdn] Khởi chạy {source.upper()} VOD trên Port {port}...")
    print(f"📁 SQLite: {db_path}")
    sys.exit(subprocess.call(cmd))

if __name__ == "__main__":
    main()
