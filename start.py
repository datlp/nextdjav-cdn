#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import time

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

VOD_SOURCES = {
    "javtiful": {"port": int(os.environ.get("PORT_JAVTIFUL", "5012"))},
    "missav":   {"port": int(os.environ.get("PORT_MISSAV", "5013"))},
    "vlxx":     {"port": int(os.environ.get("PORT_VLXX", "5014"))},
    "sextop1":  {"port": int(os.environ.get("PORT_SEXTOP1", "5015"))},
    "javguru":  {"port": int(os.environ.get("PORT_JAVGURU", "5016"))},
}

def kill_port(port: int):
    """Giết tiến trình đang chiếm port"""
    if sys.platform == "win32":
        try:
            output = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True).decode()
            for line in output.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    print(f"[*] Phát hiện PID [{pid}] đang chiếm dụng port {port}. Đang giải phóng...")
                    subprocess.call(["taskkill", "/F", "/PID", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except:
            pass
    else:
        subprocess.call(["fuser", "-k", f"{port}/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def resolve_db_path(source: str):
    prefix = "WINDOWS" if sys.platform == "win32" else "TERMUX"
    # 1. Thử lấy path đích danh từ .env
    exact_env = f"{prefix}_DB_{source.upper()}"
    if os.environ.get(exact_env) and os.path.exists(os.environ.get(exact_env)):
        return os.environ.get(exact_env)
    
    # 2. Thử lấy từ ROOT
    db_root = os.environ.get(f"{prefix}_DATABASE_ROOT")
    if db_root and os.path.exists(db_root):
        candidates = [
            os.path.join(db_root, source, f"{source}.db"),
            os.path.join(db_root, f"{source}.db")
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    return None

def main():
    env_mode = os.environ.get("APP_ENV", "prod").lower()
    port_offset = 2000 if env_mode == "dev" else 0

    nextdjav_db = os.environ.get("WINDOWS_NEXTDJAV_DB") if sys.platform == "win32" else os.environ.get("TERMUX_NEXTDJAV_DB")
    
    print(f"==================================================")
    print(f" 🚀 KHỞI CHẠY HỆ THỐNG VOD CDN (NEXTDJAV-CDN)")
    print(f"==================================================")
    print(f" Môi trường: {env_mode.upper()} | Port Offset: +{port_offset}")

    processes = []
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)

    for src, cfg in VOD_SOURCES.items():
        db_path = resolve_db_path(src)
        if not db_path:
            print(f"  [!] Bỏ qua {src.upper()}: Không tìm thấy file Database.")
            continue
            
        port = cfg["port"] + port_offset
        kill_port(port)

        cmd = [
            sys.executable,
            os.path.join(BASE_DIR, "backend", "server.py"),
            "-source", src,
            "-sqlite3", db_path,
            "-proxy-threads", "8",
            "-chunk_size", "512KB",
            "-max_connections", "30",
            "-max_keepalive", "10",
            "-timeout", "connect=3.0,read=None"
        ]
        if nextdjav_db and os.path.exists(nextdjav_db):
            cmd.extend(["-nextdjav-db", nextdjav_db])

        log_file = open(os.path.join(log_dir, f"{src}.log"), "a", encoding="utf-8")
        env_vod = os.environ.copy()
        env_vod["PORT"] = str(port)
        p = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=log_file, stderr=subprocess.STDOUT, env=env_vod)
        processes.append((src.upper(), port, p, db_path))
        print(f"  ✓ [Port {port}] VOD {src.upper():<10} -> http://localhost:{port} ({os.path.basename(db_path)})")

    if not processes:
        print("[!] Không tìm thấy database nào để khởi chạy!")
        return

    print("\n[+] Tất cả các server VOD đã sẵn sàng. Nhấn Ctrl+C để dừng.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Đang dừng hệ thống CDN VOD...")
        for name, port, p, _ in processes:
            p.terminate()
            p.wait()
        print("[✓] Đã tắt an toàn!")

if __name__ == "__main__":
    main()
