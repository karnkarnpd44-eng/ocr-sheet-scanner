import ipaddress
import logging
import os
import re
import socket
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import cv2
import easyocr
import numpy as np
from PIL import Image, ImageTk
import requests
from ultralytics import YOLO

# -------------------------------------------------------------
# ระบบ Audit Log เก็บประวัติการทำงานของร้านแยกไฟล์ตามวัน
# -------------------------------------------------------------
LOG_DIR = "system_logs"
os.makedirs(LOG_DIR, exist_ok=True)
today_log_file = os.path.join(LOG_DIR, f"audit_{datetime.now().strftime('%Y-%m-%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(today_log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("StoreAuditLogger")

def log_system_action(category, message, user="Staff"):
    """ฟังก์ชันกลางสำหรับบันทึกกิจกรรมในระบบลงไฟล์ Log"""
    entry = f"[{user}] [{category}] {message}"
    logger.info(entry)

# -------------------------------------------------------------
# Google Sheet Configuration
# -------------------------------------------------------------
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    GSHEET_AVAILABLE = True
except ImportError:
    GSHEET_AVAILABLE = False

RTSP_PATHS = [
    "/tcp/av0_0",
    "/live/ch0",
    "/onvif1",
    "/onvif2",
    "/stream1",
    "/cam/realmonitor?channel=1&subtype=0",
    "/h264/ch1/main/av_stream",
    "/live",
    "",
]
HTTP_PATHS = ["/video", "/mjpeg", "/stream", "/?action=stream"]
COMMON_PORTS = [554, 10554, 8554, 8080, 80, 8081, 1935]

SHEET_NAME = "VSearch4.0"
SHEET_TAB = "2026"

STATUS_OPTIONS = [
    "รับข้อมูลเข้าระบบแล้ว",
    "กำลังปัก",
    "รอเย็บชุดลูกเสือ",
    "รอปักดาว/จุด",
    "ปักเสร็จแล้ว",
    "ลูกค้ารับแล้ว",
    "พิมพ์เอง...",
]

STATUS_COLORS = {
    "รับข้อมูลเข้าระบบแล้ว": {"red": 0.0, "green": 0.8, "blue": 0.0},
    "กำลังปัก": {"red": 0.0, "green": 0.8, "blue": 0.0},
    "รอเย็บชุดลูกเสือ": {"red": 1.0, "green": 0.6, "blue": 0.0},
    "ปักเสร็จแล้ว": {"red": 0.0, "green": 0.8, "blue": 0.0},
    "รอปักดาว/จุด": {"red": 1.0, "green": 0.95, "blue": 0.0},
    "ลูกค้ารับแล้ว": {"red": 1.0, "green": 0.95, "blue": 0.0},
    "_custom": {"red": 0.53, "green": 0.81, "blue": 0.98},
}


class GoogleSheetManager:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.connected = False
        self._cache = {}
        self._last_sent = {}

    def connect(self, cred_path="credentials.json"):
        if not GSHEET_AVAILABLE:
            log_system_action("SHEET_CONNECT", "ล้มเหลว: ไม่พบโมดูล gspread")
            return False, "ไม่พบ gspread"
        try:
            scopes = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scopes)
            self.client = gspread.authorize(creds)
            sh = self.client.open(SHEET_NAME)
            try:
                self.sheet = sh.worksheet(SHEET_TAB)
            except Exception:
                self.sheet = sh.sheet1

            self.connected = True
            self._refresh_cache()
            msg = f"เชื่อมต่อ '{SHEET_NAME} [{self.sheet.title}]' สำเร็จ (พบ {len(self._cache)} รหัส)"
            log_system_action("SHEET_CONNECT", msg)
            return True, msg
        except Exception as e:
            err_msg = str(e)
            log_system_action("SHEET_CONNECT", f"ล้มเหลว: {err_msg}")
            return False, err_msg

    def _refresh_cache(self):
        if not self.connected:
            return
        try:
            all_values = self.sheet.get_all_values()
            self._cache = {}
            for row_idx, row in enumerate(all_values, start=1):
                if not row:
                    continue
                raw_code = str(row[0]).strip()
                status = str(row[1]).strip() if len(row) > 1 else ""
                if raw_code:
                    self._cache[raw_code] = (row_idx, status)
                    if raw_code.isdigit():
                        norm_code = str(int(raw_code))  # '0001' -> '1'
                        self._cache[norm_code] = (row_idx, status)
        except Exception as e:
            log_system_action("SHEET_CACHE", f"เกิดข้อผิดพลาดในการดึงข้อมูลแคช: {e}")

    def lookup(self, code):
        code_str = str(code).strip()
        norm_code = str(int(code_str)) if code_str.isdigit() else code_str

        if code_str in self._cache:
            return True, self._cache[code_str][0], self._cache[code_str][1]
        if norm_code in self._cache:
            return True, self._cache[norm_code][0], self._cache[norm_code][1]

        self._refresh_cache()
        if code_str in self._cache:
            return True, self._cache[code_str][0], self._cache[code_str][1]
        if norm_code in self._cache:
            return True, self._cache[norm_code][0], self._cache[norm_code][1]

        log_system_action("SHEET_LOOKUP", f"ค้นหาไม่พบรหัส: {code_str}")
        return False, -1, "ไม่พบรหัสนี้ใน Sheet"

    def update_status(self, row_index, new_status, color=None, timestamp="", source=""):
        if not self.connected:
            return False, "ยังไม่ได้เชื่อมต่อ Sheet"
        try:
            if not timestamp:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 1. บันทึกสถานะลงคอลัมน์ B (คอลัมน์ 2)
            self.sheet.update_cell(row_index, 2, new_status)

            # 2. บันทึกวันและเวลาลงคอลัมน์ G (คอลัมน์ 7)
            self.sheet.update_cell(row_index, 7, timestamp)

            # 3. บันทึกแหล่งที่มา (Auto / Manual) ลงคอลัมน์ E (คอลัมน์ 5)
            if source:
                self.sheet.update_cell(row_index, 5, source)

            # 4. หากเป็น 'ลูกค้ารับแล้ว' ให้ลงเวลาในคอลัมน์ I (คอลัมน์ 9)
            base = re.sub(r"\s*\((Auto|Manual)\)\s*$", "", new_status).strip()
            if base == "ลูกค้ารับแล้ว":
                self.sheet.update_cell(row_index, 9, timestamp)

            # 5. ปรับสีพื้นหลัง A:B ให้ตรงกับสถานะ
            bg_color = {"red": 0.97, "green": 0.44, "blue": 0.44}  # แดงสำหรับพิมพ์เอง
            if "รับข้อมูลเข้าระบบแล้ว" in new_status:
                bg_color = {"red": 0.99, "green": 0.73, "blue": 0.45}  # ส้มอ่อน
            elif "กำลังปัก" in new_status:
                bg_color = {"red": 0.99, "green": 0.94, "blue": 0.54}  # เหลืองอ่อน
            elif "รอเย็บชุดลูกเสือ" in new_status or "รอปักดาว/จุด" in new_status:
                bg_color = {"red": 0.73, "green": 0.90, "blue": 0.99}  # ฟ้าอ่อน
            elif "ปักเสร็จแล้ว" in new_status:
                bg_color = {"red": 0.73, "green": 0.97, "blue": 0.82}  # เขียวอ่อน
            elif "ลูกค้ารับแล้ว" in new_status:
                bg_color = {"red": 0.98, "green": 0.81, "blue": 0.91}  # ชมพูอ่อน

            self.sheet.format(f"A{row_index}:B{row_index}", {"backgroundColor": bg_color})
            self._refresh_cache()

            msg = f"อัปเดตแถว {row_index} → '{new_status}' แล้ว"
            log_system_action("SHEET_UPDATE", f"{msg} (แหล่งที่มา: {source or 'None'})")
            return True, msg
        except Exception as e:
            err = str(e)
            log_system_action("SHEET_UPDATE", f"เกิดข้อผิดพลาดในการอัปเดตแถว {row_index}: {err}")
            return False, err

    def notify_line_customer(self, row_index, code, new_status):
        import time
        import requests

        try:
            # 1. กำหนด safe_code ไว้บนสุด
            safe_code = str(code).strip() if code and str(code).strip() != "" else "-"
            status_text = str(new_status).strip()

            # ⚡ ตัดการส่งซ้ำ (ถ้าเป็นรหัสเดิมและสถานะเดิมที่เพิ่งยิงไปไม่ถึง 3 วินาที ให้ข้ามทันที)
            now = time.time()
            if not hasattr(self, "_last_sent"):
                self._last_sent = {}
            last_key = f"{safe_code}_{status_text}"
            if last_key in self._last_sent and (now - self._last_sent[last_key]) < 3:
                return
            self._last_sent[last_key] = now

            # 2. ดึง User ID จากคอลัมน์ H (คอลัมน์ที่ 8)
            val = self.sheet.cell(row_index, 8).value
            user_id = str(val).strip() if val else ""

            if not user_id or user_id.lower() in ["none", "null", ""]:
                log_system_action("LINE_NOTIFY", f"ข้ามแถว {row_index} รหัส {safe_code} เนื่องจากไม่มี User ID ในคอลัมน์ H")
                return

            channel_token = "L7RIVNhGtYj0lNv3iA0ISVllSO8xiv7+OU0ZCB8Yfp+3wlqxfsET0m5HtOtadzKxZf4Fbfwbg2obz1IKElIEyEmeHYl4JHM4rM9PYSz4EnEt92vG5jExY6YMWRAMrap7iKNsOoU/TKz9i3XbynnV6QdB04t89/1O/w1cDnyilFU="
            url = "https://api.line.me/v2/bot/message/push"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {channel_token}",
            }

            display_status = status_text if status_text else "ข้อมูลยังไม่เข้าระบบ"
            status_color = "#94A3B8"
            status_icon = "⏳"

            if not status_text or status_text in ["null", "None"]:
                display_status = "ข้อมูลยังไม่เข้าระบบ"
                status_color = "#94A3B8"
                status_icon = "⏳"
            elif "รับข้อมูลเข้าระบบแล้ว" in status_text:
                status_color = "#EA580C"  # ส้ม
                status_icon = "📥"
            elif "กำลังปัก" in status_text:
                status_color = "#EAB308"  # เหลือง
                status_icon = "🧵"
            elif "รอเย็บชุดลูกเสือ" in status_text:
                status_color = "#0284C7"  # ฟ้า
                status_icon = "🪡"
            elif "รอปักดาว/จุด" in status_text:
                status_color = "#0284C7"  # ฟ้า
                status_icon = "⭐"
            elif "ปักเสร็จแล้ว" in status_text:
                status_color = "#16A34A"  # เขียว
                status_icon = "✅"
            elif "ลูกค้ารับแล้ว" in status_text:
                status_color = "#EC4899"  # ชมพู
                status_icon = "🛍️"
            else:
                status_color = "#DC2626"  # แดง
                status_icon = "✏️"

            payload = {
                "to": user_id,
                "messages": [
                    {
                        "type": "flex",
                        "altText": f"🧵 ว.เซ็นเตอร์ แจ้งเตือน: รหัส {safe_code} สถานะ {display_status}",
                        "contents": {
                            "type": "bubble",
                            "size": "giga",
                            "direction": "ltr",
                            "header": {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {"type": "text", "text": "🧵 ว.เซ็นเตอร์ 🧵", "weight": "bold", "size": "3xl", "color": "#F7690A", "align": "center", "wrap": False},
                                    {"type": "text", "text": "ถูกครบ จบที่เดียว", "weight": "bold", "size": "xs", "color": "#94A3B8", "align": "center", "wrap": False},
                                    {"type": "separator", "margin": "md"},
                                ],
                            },
                            "hero": {
                                "type": "image",
                                "url": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQZ72HAyev-Fr5x-3uwbKg2uKWDzsuFeyeTnxVEF3g3bvCWDZne2mYKKZA&s=10",
                                "size": "full",
                                "aspectRatio": "1.51:1",
                                "aspectMode": "fit",
                            },
                            "body": {
                                "type": "box",
                                "layout": "vertical",
                                "spacing": "md",
                                "contents": [
                                    {"type": "text", "text": "📢 แจ้งเตือนสถานะงานปัก", "weight": "bold", "color": "#1E3A8A", "align": "center", "size": "lg"},
                                    {
                                        "type": "box",
                                        "layout": "vertical",
                                        "margin": "md",
                                        "spacing": "sm",
                                        "backgroundColor": "#F8FAFC",
                                        "cornerRadius": "md",
                                        "paddingAll": "md",
                                        "contents": [
                                            {
                                                "type": "box",
                                                "layout": "baseline",
                                                "spacing": "sm",
                                                "contents": [
                                                    {"type": "text", "text": "🏷️ รหัสงาน :", "color": "#64748B", "size": "md", "flex": 2},
                                                    {"type": "text", "text": safe_code, "weight": "bold", "color": "#0F172A", "size": "lg", "flex": 3},
                                                ],
                                            },
                                            {
                                                "type": "box",
                                                "layout": "baseline",
                                                "spacing": "sm",
                                                "contents": [
                                                    {"type": "text", "text": f"{status_icon} สถานะ :", "color": "#64748B", "size": "md", "flex": 2},
                                                    {"type": "text", "text": display_status, "weight": "bold", "color": status_color, "size": "lg", "flex": 3},
                                                ],
                                            },
                                        ],
                                    },
                                    {"type": "text", "text": "ขอบคุณที่ใช้บริการ ว.เซ็นเตอร์ ครับ/ค่ะ 🙏", "color": "#94A3B8", "size": "xs", "align": "center", "margin": "sm"},
                                ],
                            },
                            "footer": {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {
                                        "type": "button",
                                        "style": "primary",
                                        "color": "#F7690A",
                                        "action": {
                                            "type": "message",
                                            "label": "🔄 เช็คสถานะอีกครั้ง",
                                            "text": safe_code,
                                        },
                                    }
                                ],
                            },
                        },
                    }
                ],
            }

            resp = requests.post(url, headers=headers, json=payload, timeout=5)
            log_system_action("LINE_NOTIFY", f"ส่งแจ้งเตือนแถว {row_index} รหัส {safe_code} | HTTP {resp.status_code}")
        except Exception as e:
            log_system_action("LINE_NOTIFY", f"เกิดข้อผิดพลาดในการส่ง LINE รหัส {code}: {e}")

class OCRScannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("YOLO Real-Time Tracker + Google Sheet")
        self.root.geometry("1400x960")
        self.current_frame = None
        self.is_streaming = False
        self.cap = None
        self.camera_index = 2
        self.found_cameras = []
        self.found_ip_cameras = []
        
        self.gs = GoogleSheetManager()
        self.zoom_level = 1.0
        self.zoom_min = 1.0
        self.zoom_max = 5.0
        self.zoom_step = 0.25

        self._undo_stack = []
        self._blink_job = None
        self._indicator_state = "idle"

        # ตัวแปรควบคุมระบบล็อกเป้าและรีเช็ก
        self.candidate_code = None
        self.candidate_count = 0
        self.locked_code = None
        self.is_verifying = False

        # เริ่มต้นบันทึก Log การเปิดโปรแกรม
        log_system_action("APP_START", "เริ่มต้นเปิดใช้งานโปรแกรม OCRScannerApp")

        # โหลดโมเดล
        print("กำลังโหลด YOLO & EasyOCR...")
        log_system_action("MODEL_INIT", "กำลังโหลดโมเดล YOLO & EasyOCR...")
        self.yolo_model = YOLO("best1.pt") if os.path.exists("best1.pt") else None
        self.reader = easyocr.Reader(['en'], gpu=False)
        print("✅ โมเดลพร้อมทำงาน")
        log_system_action("MODEL_INIT", "โมเดล YOLO & EasyOCR พร้อมทำงานเรียบร้อย")

        self._build_ui()
        self._scan_cameras()

    # ─────────────────────────── UI ───────────────────────────
    def _build_ui(self):
        top = tk.Frame(self.root, pady=6)
        top.pack(fill="x", padx=10)

        tk.Label(top, text="แหล่งกล้อง:").pack(side="left")
        self.source_type = ttk.Combobox(
            top, values=["Webcam", "IP Camera (LAN)", "URL โดยตรง"], width=18, state="readonly"
        )
        self.source_type.current(2)
        self.source_type.pack(side="left", padx=4)
        self.source_type.bind("<<ComboboxSelected>>", self._on_source_change)

        self.url_frame = tk.Frame(top)
        self.url_entry = tk.Entry(self.url_frame, width=48)
        self.url_entry.insert(0, "rtsp://admin:Sitthik0rn@192.168.1.124:10554/tcp/av0_0")
        self.url_entry.pack(side="left")
        tk.Button(
            self.url_frame, text="🔗 เชื่อมต่อ", bg="#0078d4", fg="white", command=self.start_stream
        ).pack(side="left", padx=2)
        self.url_frame.pack(side="left", padx=4)

        for text, color, cmd in [
            ("▶ เริ่มตรวจจับสด", "green", self.start_stream),
            ("⏹ หยุด", "red", self.stop_stream),
            ("💾 บันทึกผล", "#2a7a2a", self.save_result),
            ("🗑 ล้างข้อมูล", "#888", self.clear_result),
            ("🔄 รีโหลด", "#8B4513", self.reload_app),
        ]:
            tk.Button(top, text=text, bg=color, fg="white", font=("Segoe UI", 9, "bold"), command=cmd).pack(
                side="left", padx=3
            )

        # Row 2: webcam
        self.row_webcam = tk.Frame(self.root, pady=2)
        tk.Label(self.row_webcam, text="เลือกกล้อง:").pack(side="left")
        self.camera_combo = ttk.Combobox(self.row_webcam, values=[], width=28, state="readonly")
        self.camera_combo.pack(side="left", padx=4)
        self.camera_combo.bind("<<ComboboxSelected>>", self._on_camera_select)
        tk.Button(self.row_webcam, text="🔍 ค้นหากล้อง", command=self._scan_cameras).pack(side="left", padx=4)

        # Row 3: IP scanner
        self.row_ip = tk.Frame(self.root, pady=2)
        tk.Label(self.row_ip, text="IP Range:").pack(side="left")
        self.ip_range_entry = tk.Entry(self.row_ip, width=18)
        self.ip_range_entry.insert(0, self._get_local_subnet())
        self.ip_range_entry.pack(side="left", padx=4)
        tk.Label(self.row_ip, text="User:").pack(side="left")
        self.cam_user = tk.Entry(self.row_ip, width=8)
        self.cam_user.insert(0, "admin")
        self.cam_user.pack(side="left", padx=2)
        tk.Label(self.row_ip, text="Pass:").pack(side="left")
        self.cam_pass = tk.Entry(self.row_ip, width=8, show="*")
        self.cam_pass.insert(0, "admin")
        self.cam_pass.pack(side="left", padx=2)
        tk.Button(self.row_ip, text="🌐 สแกน LAN", bg="#7b2d8b", fg="white", command=self._scan_ip_cameras).pack(side="left", padx=4)
        self.scan_progress = tk.Label(self.row_ip, text="", fg="#0078d4")
        self.scan_progress.pack(side="left", padx=4)
        self.ip_cam_combo = ttk.Combobox(self.row_ip, values=[], width=48, state="readonly")
        self.ip_cam_combo.pack(side="left", padx=4)
        self.ip_cam_combo.bind("<<ComboboxSelected>>", self._on_ip_cam_select)

        # Row 4: Zoom Controls
        ctrl = tk.Frame(self.root, pady=3, bg="#f0f0f0")
        ctrl.pack(fill="x", padx=10)
        tk.Label(ctrl, text="  🔍 ซูมกล้อง:", bg="#f0f0f0").pack(side="left", padx=(8, 2))
        tk.Button(ctrl, text="➕", width=3, command=self._zoom_in, bg="#ddd").pack(side="left", padx=1)
        self.zoom_label = tk.Label(ctrl, text="1.0x", width=5, bg="#f0f0f0", font=("Segoe UI", 9, "bold"))
        self.zoom_label.pack(side="left")
        tk.Button(ctrl, text="➖", width=3, command=self._zoom_out, bg="#ddd").pack(side="left", padx=1)
        tk.Button(ctrl, text="รีเซ็ตซูม", command=self._zoom_reset, bg="#ddd").pack(side="left", padx=4)
        tk.Label(ctrl, text="| ระบบล็อกเป้าอัตโนมัติ (อ่านติดกัน 2 ครั้ง ยืนยันแล้วหยุดอ่านซ้ำ)", fg="#0055cc", bg="#f0f0f0", font=("Segoe UI", 9, "bold")).pack(side="left", padx=10)

        # Row 5: Google Sheet
        gs_frame = tk.Frame(self.root, pady=3, bg="#e8f4e8")
        gs_frame.pack(fill="x", padx=10)
        tk.Label(gs_frame, text="🟢 Google Sheet:", font=("Segoe UI", 9, "bold"), bg="#e8f4e8").pack(side="left")
        tk.Label(gs_frame, text="credentials.json:", bg="#e8f4e8").pack(side="left", padx=(8, 2))
        self.cred_path = tk.Entry(gs_frame, width=24)
        self.cred_path.insert(0, "credentials.json")
        self.cred_path.pack(side="left", padx=2)
        tk.Button(gs_frame, text="📁", command=self._browse_cred).pack(side="left")
        tk.Button(gs_frame, text="🔌 เชื่อมต่อ Sheet", bg="#1a7340", fg="white", command=self._connect_sheet).pack(side="left", padx=6)
        tk.Label(gs_frame, text="สถานะที่จะอัปเดต:", bg="#e8f4e8", font=("Segoe UI", 12, "bold")).pack(side="left", padx=(8, 2))
        self.status_var = tk.StringVar(value=STATUS_OPTIONS[0])
        self.status_combo = ttk.Combobox(gs_frame, textvariable=self.status_var, values=STATUS_OPTIONS, width=25, state="readonly")
        self.status_combo.pack(side="left", padx=4)
        self.status_combo.bind("<<ComboboxSelected>>", self._on_status_select)
        self.custom_status_entry = tk.Entry(gs_frame, width=20, fg="#555")
        self.custom_status_entry.insert(0, "พิมพ์สถานะที่ต้องการ...")
        self.gs_status = tk.Label(gs_frame, text="ยังไม่ได้เชื่อมต่อ", fg="gray", bg="#e8f4e8", font=("Segoe UI", 9))
        self.gs_status.pack(side="left", padx=6)

        # Row 6: Manual code entry
        manual_frame = tk.Frame(self.root, pady=4, bg="#eef4ff", relief="groove", bd=1)
        manual_frame.pack(fill="x", padx=10, pady=(0, 0))
        tk.Label(manual_frame, text="✏️ ป้อนรหัส 4 หลักเอง:", font=("Segoe UI", 9, "bold"), bg="#eef4ff").pack(side="left", padx=(8, 4))
        self.manual_code_entry = tk.Entry(manual_frame, width=10, font=("Segoe UI", 11, "bold"), justify="center")
        self.manual_code_entry.pack(side="left", padx=4)
        self.manual_code_entry.bind("<Return>", self._on_manual_code_enter)
        self.manual_code_entry.bind("<KeyRelease>", self._limit_manual_entry)
        tk.Button(manual_frame, text="✅ อัปเดต", bg="#1a7340", fg="white", font=("Segoe UI", 9, "bold"), command=self._on_manual_code_enter).pack(side="left", padx=4)
        
        self.indicator_canvas = tk.Canvas(manual_frame, width=32, height=32, bg="#eef4ff", highlightthickness=0)
        self.indicator_canvas.pack(side="left", padx=4)
        self._draw_indicator("idle")

        self.undo_btn = tk.Button(manual_frame, text="↩ Undo", bg="#c0392b", fg="white", font=("Segoe UI", 9, "bold"), state="disabled", command=self._undo_last)
        self.undo_btn.pack(side="left", padx=6)
        self.undo_info_label = tk.Label(manual_frame, text="", font=("Segoe UI", 8), bg="#eef4ff", fg="#888")
        self.undo_info_label.pack(side="left", padx=4)

        # Row 7: Last Update Banner
        last_update_frame = tk.Frame(self.root, bg="#1a1a2e", pady=6)
        last_update_frame.pack(fill="x", padx=10, pady=(0, 2))
        tk.Label(last_update_frame, text="LAST UPDATE", font=("Segoe UI", 9, "bold"), bg="#1a1a2e", fg="#7faaff").pack(side="left", padx=(12, 6))
        self.last_update_label = tk.Label(last_update_frame, text="—  รอการตรวจจับตัวเลข 4 หลัก", font=("Segoe UI", 16, "bold"), bg="#1a1a2e", fg="#ffffff", anchor="w")
        self.last_update_label.pack(side="left", padx=4, fill="x", expand=True)
        self.last_update_time_label = tk.Label(last_update_frame, text="", font=("Segoe UI", 10), bg="#1a1a2e", fg="#aaaaaa", anchor="e")
        self.last_update_time_label.pack(side="right", padx=12)

        # =========================================================================
        # โซนแสดงผลตรงกลาง (PanedWindow: สามารถคลิกลากปรับขนาด ซ้าย-ขวา ได้อิสระ)
        # =========================================================================
        self.paned_window = tk.PanedWindow(
            self.root,
            orient="horizontal",
            sashrelief="raised",
            sashwidth=6,
            sashpad=2,
            bg="#334155"
        )
        self.paned_window.pack(fill="both", expand=True, padx=10, pady=4)

        # 1. ฝั่งซ้าย: กล้อง Canvas
        self.canvas = tk.Canvas(self.paned_window, bg="black", highlightthickness=0)
        self.paned_window.add(self.canvas, minsize=300)
        self.canvas.create_text(350, 250, text="กดปุ่ม '▶ เริ่มตรวจจับสด' เพื่อเปิดกล้อง", fill="white", font=("Segoe UI", 14))

        # 2. ฝั่งขวา: แผงควบคุม Google Sheet
        sheet_panel = tk.Frame(self.paned_window, bg="#1e293b", padx=10, pady=10, relief="flat")
        self.paned_window.add(sheet_panel, minsize=200)

        # หัวข้อแผงขวา
        panel_header = tk.Frame(sheet_panel, bg="#1e293b")
        panel_header.pack(fill="x", pady=(0, 8))
        tk.Label(
            panel_header, 
            text="📊 ประวัติการบันทึกลง Google Sheet", 
            font=("Segoe UI", 11, "bold"), 
            bg="#1e293b", 
            fg="#38bdf8"
        ).pack(side="left")

        # กล่องแสดงผล Google Sheet
        self.sheet_result = scrolledtext.ScrolledText(
            sheet_panel,
            font=("Consolas", 10),
            bg="#9fb7f0",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            padx=10,
            pady=10,
            highlightthickness=1,
            highlightbackground="#BAD2F3"
        )
        self.sheet_result.pack(fill="both", expand=True)

        # ตั้งค่าเริ่มต้นให้เส้นแบ่งอยู่ที่สัดส่วน 70% อัตโนมัติเมื่อหน้าจอเปิดขึ้นมา
        def _set_default_sash():
            try:
                total_w = self.paned_window.winfo_width()
                if total_w > 100:
                    self.paned_window.sash_place(0, int(total_w * 0.70), 0)
            except Exception:
                pass
        self.root.after(200, _set_default_sash)

        # =========================================================================
        # โซนด้านล่าง: ล็อกการตรวจจับ YOLO + EasyOCR ขยายเต็มพื้นที่
        # =========================================================================
        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 4))
        
        tk.Label(bottom, text="📝 ล็อกการตรวจจับ (YOLO + EasyOCR):", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.result_text = scrolledtext.ScrolledText(bottom, height=6, font=("Consolas", 11))
        self.result_text.pack(fill="both", expand=True)

        self.status = tk.Label(self.root, text="พร้อมทำงาน", fg="green", anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0, 4))

    # ─────────────────────────── INDICATOR ───────────────────────────
    def _draw_indicator(self, state, visible=True):
        self.indicator_canvas.delete("all")
        colors = {
            "idle": ("#cccccc", "#999999"),
            "success": ("#00cc44", "#008833"),
            "error": ("#ff3333", "#cc0000"),
        }
        fill, outline = colors.get(state, colors["idle"])
        if not visible and state != "idle":
            fill = "#eef4ff"
            outline = "#eef4ff"
        self.indicator_canvas.create_oval(4, 4, 24, 24, fill=fill, outline=outline, width=2)

    def _start_blink(self, state):
        self._indicator_state = state
        if self._blink_job:
            self.root.after_cancel(self._blink_job)
            self._blink_job = None
        self._blink_count = 0
        self._blink_cycle(state)

    def _blink_cycle(self, state, on=True):
        if self._blink_count >= 10:
            self._draw_indicator(state, visible=True)
            self._blink_job = None
            return
        self._draw_indicator(state, visible=on)
        self._blink_count += 1
        self._blink_job = self.root.after(300, lambda: self._blink_cycle(state, not on))

    def _set_indicator_success(self, code, action="อัปเดต"):
        ts = datetime.now().strftime("%H:%M:%S")
        msg = f"รหัส {code} → {action}สำเร็จ!"
        self.last_update_label.config(text=f"✅ {msg}", fg="#00ff88")
        self.last_update_time_label.config(text=ts, fg="#aaaaaa")
        self._start_blink("success")
        log_system_action("INDICATOR", f"STATUS_SUCCESS: {msg}")

    def _set_indicator_error(self, msg="อัปเดตไม่สำเร็จ"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._start_blink("error")
        self.last_update_label.config(text=f"❌ {msg}", fg="#ff5555")
        self.last_update_time_label.config(text=ts, fg="#aaaaaa")
        log_system_action("INDICATOR", f"STATUS_ERROR: {msg}")

   # ─────────────────────────── GOOGLE SHEET ───────────────────────────
    def _browse_cred(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            self.cred_path.delete(0, "end")
            self.cred_path.insert(0, path)
            log_system_action("CONFIG", f"เลือกไฟล์ Service Account Credentials: {path}")

    def _connect_sheet(self):
        self.gs_status.config(text="กำลังเชื่อมต่อ...", fg="orange")
        self.root.update()
        cred_file = self.cred_path.get().strip()
        log_system_action("USER_ACTION", f"กดปุ่มเชื่อมต่อ Google Sheet ด้วยไฟล์ '{cred_file}'")
        ok, msg = self.gs.connect(cred_file)
        if ok:
            self.gs_status.config(text=f"✅ {msg}", fg="green")
        else:
            self.gs_status.config(text=f"❌ {msg}", fg="red")

    def _lookup_and_update(self, code, source="Auto"):
        if not self.gs.connected:
            self.sheet_result.insert("end", "⚠️ ยังไม่ได้เชื่อมต่อ Sheet\n")
            self.sheet_result.see("end")
            log_system_action("UPDATE_WARN", f"ไม่สามารถอัปเดตรหัส {code} ได้เนื่องจากยังไม่ได้เชื่อมต่อ Sheet")
            return
        
        base_status = self._get_current_status()
        is_custom_mode = (self.status_var.get() == "พิมพ์เอง...")
        new_status = f"{base_status} (Auto)" if (source == "Auto" and not is_custom_mode) else base_status
        color = self._get_current_color()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        found, row_idx, current_status = self.gs.lookup(code)
        if found:
            # เงื่อนไขเช็คสถานะซ้ำในคอลัมน์ B (ยกเว้นโหมด พิมพ์เอง...)
            if not is_custom_mode:
                current_base = re.sub(r"\s*\((Auto|Manual)\)\s*$", "", current_status).strip()
                if current_base == base_status.strip():
                    line = f"ℹ️ [{ts}] รหัส {code} มีสถานะ '{current_status}' อยู่แล้ว → ข้ามการอัปเดตซ้ำ\n"
                    self.sheet_result.insert("end", line, "skip")
                    self.sheet_result.tag_config("skip", foreground="#f01303")
                    self.sheet_result.see("end")
                    self._set_indicator_error(f"รหัส {code} มีสถานะนี้อยู่แล้ว")
                    log_system_action("UPDATE_SKIP", f"ข้ามรหัส {code} เนื่องจากมีสถานะ '{current_status}' อยู่แล้ว")
                    return

            ok, update_msg = self.gs.update_status(row_idx, new_status, color, timestamp=ts, source=source)
            if ok:
                threading.Thread(target=self.gs.notify_line_customer, args=(row_idx, code, new_status), daemon=True).start()
                self._undo_stack.append({
                    "code": code, "row_idx": row_idx, "old_status": current_status,
                    "new_status": new_status, "old_color": self._status_to_color(current_status)
                })
                line = f"✅ [{ts}] รหัส {code} (แถว {row_idx})\n   {current_status} → {new_status}\n"
                self.sheet_result.insert("end", line, "updated")
                self._set_indicator_success(code)
                self._update_undo_ui()
                log_system_action("STATUS_SUCCESS", f"อัปเดตสำเร็จ: รหัส {code} แถว {row_idx} ({current_status} → {new_status}) [ที่มา: {source}]")
            else:
                self.sheet_result.insert("end", f"⚠️ รหัส {code} อัปเดตไม่ได้: {update_msg}\n", "warn")
                self._set_indicator_error()
                log_system_action("STATUS_FAIL", f"อัปเดตล้มเหลว: รหัส {code} ข้อผิดพลาด: {update_msg}")
        else:
            self.sheet_result.insert("end", f"❌ [{ts}] รหัส {code} → ไม่พบในตาราง Sheet\n", "notfound")
            self._set_indicator_error(f"ไม่พบรหัส {code} ใน Sheet")
            log_system_action("STATUS_NOTFOUND", f"ไม่พบรหัส {code} ใน Google Sheet")

        self.sheet_result.tag_config("updated", foreground="#007031")
        self.sheet_result.tag_config("warn", foreground="orange")
        self.sheet_result.tag_config("notfound", foreground="red")
        self.sheet_result.see("end")

    # ─────────────────────────── STREAM & YOLO TRACKING ───────────────────────────
    def _apply_zoom(self, frame):
        if self.zoom_level <= 1.0:
            return frame
        h, w = frame.shape[:2]
        new_w, new_h = int(w / self.zoom_level), int(h / self.zoom_level)
        x1 = max(0, int((w - new_w) / 2))
        y1 = max(0, int((h - new_h) / 2))
        cropped = frame[y1:y1 + new_h, x1:x1 + new_w]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    def start_stream(self):
        if self.is_streaming:
            return
        src_type = self.source_type.get()
        src = self.camera_index if src_type == "Webcam" else self.url_entry.get().strip()
        log_system_action("STREAM_CONTROL", f"กดปุ่มเริ่มตรวจจับสด: {src_type} ({src})")
        self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW if isinstance(src, int) else 0)
        if not self.cap.isOpened():
            log_system_action("STREAM_ERROR", f"เปิดกล้องไม่สำเร็จ: {src}")
            messagebox.showerror("Error", f"เปิดกล้องไม่ได้:\n{src}")
            return
        self.is_streaming = True
        threading.Thread(target=self._stream_loop, daemon=True).start()
        self.status.config(text="🟢 กำลังสตรีมภาพและเปิดโหมด YOLO ตรวจจับอัตโนมัติ")

    def stop_stream(self):
        if self.is_streaming:
            log_system_action("STREAM_CONTROL", "กดปุ่มหยุดการสตรีมและตรวจจับ")
        self.is_streaming = False
        if self.cap:
            self.cap.release()
        self.status.config(text="หยุดการทำงานแล้ว")

    def _stream_loop(self):
        frame_counter = 0
        detected_boxes = []

        while self.is_streaming:
            ret, raw_frame = self.cap.read()
            if not ret or raw_frame is None or raw_frame.size == 0:
                time.sleep(0.02)
                continue

            frame = self._apply_zoom(raw_frame)
            self.current_frame = frame
            display_frame = frame.copy()
            h, w = frame.shape[:2]
            frame_counter += 1

            # วิ่ง YOLO ทุก 3 เฟรม
            if self.yolo_model is not None and frame_counter % 3 == 0:
                results = self.yolo_model(frame, conf=0.30, verbose=False)
                new_boxes = []
                for r in results:
                    for box in r.boxes:
                        # ตรวจสอบคลาส รองรับทั้ง 'code' และ 'code-detection'
                        cls_id = int(box.cls[0])
                        cls_name = r.names.get(cls_id, "")
                        if cls_name in ["code", "code-detection", ""]:
                            bx1, by1, bx2, by2 = map(int, box.xyxy[0])
                            new_boxes.append((bx1, by1, bx2, by2))
                detected_boxes = new_boxes

                # หากพบวัตถุ และยังไม่ได้อยู่ในสถานะกำลัง OCR ให้ส่งภาพไปตรวจ
                if detected_boxes and not self.is_verifying:
                    bx1, by1, bx2, by2 = detected_boxes[0]
                    pad = 10
                    cx1 = max(0, bx1 - pad)
                    cy1 = max(0, by1 - pad)
                    cx2 = min(w, bx2 + pad)
                    cy2 = min(h, by2 + pad)
                    crop_target = frame[cy1:cy2, cx1:cx2]

                    if crop_target.size > 0:
                        threading.Thread(target=self._verify_and_process, args=(crop_target,), daemon=True).start()

            # วาดกรอบบนหน้าจอ
            for (bx1, by1, bx2, by2) in detected_boxes:
                box_color = (255, 120, 0) if self.locked_code else (0, 255, 0)
                cv2.rectangle(display_frame, (bx1, by1), (bx2, by2), box_color, 2)
                
                label_text = f"Locked: {self.locked_code}" if self.locked_code else f"Detect: {self.candidate_code or 'Scanning...'}"
                cv2.putText(display_frame, label_text, (bx1, max(25, by1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, box_color, 2)

            self._update_canvas(display_frame)
            time.sleep(0.01)

    def _preprocess_for_ocr(self, img):
        """เพิ่มความคมชัดและลดแสงสะท้อนเพื่อให้ EasyOCR อ่านตัวเลข 4 หลักได้แม่นยำขึ้น"""
        try:
            # ขยายภาพ 2 เท่า เพื่อให้ตัวเลขคมชัดขึ้น
            resized = cv2.resize(img, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            # ปรับเพิ่ม Contrast ด้วย CLAHE
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            return enhanced
        except Exception:
            return img

    def _verify_and_process(self, crop_img):
        self.is_verifying = True
        try:
            processed_img = self._preprocess_for_ocr(crop_img)
            ocr_res = self.reader.readtext(processed_img, allowlist='0123456789')
            found_4digit = None

            for _, text, prob in ocr_res:
                clean = text.strip()
                # กรองตัวเลข 4 หลัก โดยมั่นใจว่าความน่าจะเป็นผ่านเกณฑ์
                if re.fullmatch(r'\d{4}', clean) and prob > 0.35:
                    found_4digit = clean
                    break

            if found_4digit:
                # ถ้ารหัสที่อ่านได้ ตรงกับรหัสที่เพิ่งอัปเดตไป (locked) ให้ข้ามเลยจนกว่าจะเปลี่ยนถุง
                if found_4digit == self.locked_code:
                    self.is_verifying = False
                    return

                # สลับไปเจอถุงใหม่ ให้ปลดล็อกรหัสเดิมทันที
                if self.locked_code and found_4digit != self.locked_code:
                    self.locked_code = None

                # ตรวจสอบซ้ำ 2 รอบติดกัน (Double-Check)
                if self.candidate_code == found_4digit:
                    self.candidate_count += 1
                else:
                    self.candidate_code = found_4digit
                    self.candidate_count = 1

                log = f"[Detect] ตรวจพบตัวเลข {found_4digit} (ยืนยัน {self.candidate_count}/2)\n"
                self.root.after(0, lambda: self.result_text.insert("end", log))
                self.root.after(0, lambda: self.result_text.see("end"))

                # ยืนยันครบ 2 ครั้ง
                if self.candidate_count >= 2:
                    self.locked_code = found_4digit  # ล็อกเป้าหมาย ไม่ให้อ่านซ้ำ
                    log_system_action("DETECTION", f"ยืนยันรหัส {found_4digit} ครบ 2 ครั้ง (Double-Check Passed) → สั่งอัปเดต Auto")
                    
                    confirm_log = f"🎯 [CONFIRMED] ยืนยันรหัส {found_4digit} → กำลังส่งไป Sheet...\n{'-'*40}\n"
                    self.root.after(0, lambda: self.result_text.insert("end", confirm_log))
                    self.root.after(0, lambda: self.result_text.see("end"))
                    self.root.after(0, lambda c=found_4digit: self._lookup_and_update(c, source="Auto"))

                    self.candidate_code = None
                    self.candidate_count = 0
            else:
                self.candidate_count = 0
        except Exception as e:
            log_system_action("DETECTION_ERROR", f"เกิดข้อผิดพลาดในการตรวจสอบ OCR: {e}")
        finally:
            self.is_verifying = False

    def _update_canvas(self, frame):
        try:
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            
            if cw < 50 or ch < 50:
                cw, ch = 800, 500

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            fh, fw = rgb.shape[:2]

            scale = min(cw / fw, ch / fh)
            dw, dh = max(1, int(fw * scale)), max(1, int(fh * scale))

            resized = cv2.resize(rgb, (dw, dh), interpolation=cv2.INTER_LINEAR)
            img = ImageTk.PhotoImage(Image.fromarray(resized))
            
            self.root.after(0, lambda i=img, w=dw, h=dh, cw=cw, ch=ch: self._show_canvas(i, w, h, cw, ch))
        except Exception:
            pass

    def _show_canvas(self, img, w, h, cw, ch):
        self.canvas.delete("all")
        pos_x = (cw - w) // 2
        pos_y = (ch - h) // 2
        self.canvas.create_image(pos_x, pos_y, anchor="nw", image=img)
        self.canvas.image = img

    # ─────────────────────────── MANUAL ENTRY & OTHER ───────────────────────────
    def _limit_manual_entry(self, event=None):
        val = self.manual_code_entry.get()
        cleaned = re.sub(r"[^0-9]", "", val)[:4]
        if cleaned != val:
            self.manual_code_entry.delete(0, "end")
            self.manual_code_entry.insert(0, cleaned)

    def _on_manual_code_enter(self, event=None):
        code = self.manual_code_entry.get().strip()
        if not re.fullmatch(r"\d{4}", code):
            messagebox.showwarning("รหัสไม่ถูกต้อง", "กรุณาพิมพ์ตัวเลข 4 หลัก เช่น 2491")
            return
        if not self.gs.connected:
            messagebox.showwarning("ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อ Google Sheet ก่อน")
            return
        log_system_action("USER_ACTION", f"พิมพ์รหัสเอง (Manual Entry): {code}")
        threading.Thread(target=lambda: self._lookup_and_update(code, source="Manual"), daemon=True).start()
        self.manual_code_entry.delete(0, "end")

    def _status_to_color(self, status_text):
        return STATUS_COLORS.get(status_text, STATUS_COLORS["_custom"])

    def _update_undo_ui(self):
        if self._undo_stack:
            last = self._undo_stack[-1]
            self.undo_btn.config(state="normal")
            self.undo_info_label.config(text=f"← รหัส {last['code']}: '{last['new_status']}' → '{last['old_status']}'")
        else:
            self.undo_btn.config(state="disabled")
            self.undo_info_label.config(text="")

    def _undo_last(self):
        if not self._undo_stack or not self.gs.connected:
            return
        entry = self._undo_stack.pop()
        log_system_action("USER_ACTION", f"กดปุ่ม ↩ Undo ย้อนกลับสถานะรหัส {entry['code']}")
        threading.Thread(target=self._undo_worker, args=(entry,), daemon=True).start()

    def _undo_worker(self, entry):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old_color = entry.get("old_color", STATUS_COLORS["_custom"])
        ok, msg = self.gs.update_status(entry["row_idx"], entry["old_status"], old_color, timestamp=ts)
        if ok:
            line = f"↩ [{ts}] Undo: รหัส {entry['code']} กลับเป็น '{entry['old_status']}'\n"
            self.root.after(0, lambda: self.sheet_result.insert("end", line, "updated"))
            self.root.after(0, lambda: self._set_indicator_success(entry['code'], action="ย้อนกลับ"))
            self.root.after(0, self._update_undo_ui)
            log_system_action("STATUS_UNDO", f"ย้อนสถานะรหัส {entry['code']} สำเร็จ (กลับเป็น '{entry['old_status']}')")

    def _zoom_in(self):
        self.zoom_level = min(self.zoom_max, round(self.zoom_level + self.zoom_step, 2))
        self.zoom_label.config(text=f"{self.zoom_level:.1f}x")
        log_system_action("USER_ACTION", f"ปรับซูมเข้า (+): {self.zoom_level:.1f}x")

    def _zoom_out(self):
        self.zoom_level = max(self.zoom_min, round(self.zoom_level - self.zoom_step, 2))
        self.zoom_label.config(text=f"{self.zoom_level:.1f}x")
        log_system_action("USER_ACTION", f"ปรับซูมออก (-): {self.zoom_level:.1f}x")

    def _zoom_reset(self):
        self.zoom_level = 1.0
        self.zoom_label.config(text="1.0x")
        log_system_action("USER_ACTION", "รีเซ็ตระยะซูมกล้องเป็น 1.0x")

    def _on_source_change(self, event=None):
        src = self.source_type.get()
        log_system_action("CONFIG", f"เปลี่ยนแหล่งกล้องเป็น: {src}")
        self.row_webcam.pack_forget()
        self.row_ip.pack_forget()
        self.url_frame.pack_forget()
        if src == "Webcam":
            self.row_webcam.pack(fill="x", padx=10, pady=2)
        elif src == "IP Camera (LAN)":
            self.row_ip.pack(fill="x", padx=10, pady=2)
        elif src == "URL โดยตรง":
            self.url_frame.pack(side="left", padx=4)

    def _on_status_select(self, event=None):
        sel = self.status_var.get()
        log_system_action("CONFIG", f"เลือกสถานะที่จะอัปเดต: {sel}")
        if sel == "พิมพ์เอง...":
            self.custom_status_entry.pack(side="left", padx=4)
        else:
            self.custom_status_entry.pack_forget()

    def _get_current_status(self):
        selected = self.status_var.get()
        if selected == "พิมพ์เอง...":
            custom = self.custom_status_entry.get().strip()
            return custom if custom and custom != "พิมพ์สถานะที่ต้องการ..." else "ปักเสร็จแล้ว"
        return selected

    def _get_current_color(self):
        selected = self.status_var.get()
        return STATUS_COLORS["_custom"] if selected == "พิมพ์เอง..." else STATUS_COLORS.get(selected, STATUS_COLORS["_custom"])

    def _scan_cameras(self):
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        found = []
        for i in range(4):
            try:
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        found.append((i, f"กล้อง {i}"))
                cap.release()
            except Exception:
                pass
        self.root.after(0, lambda: self._update_camera_list(found))

    def _update_camera_list(self, found):
        self.found_cameras = found
        if found:
            self.camera_combo["values"] = [f[1] for f in found]
            self.camera_combo.current(0)
            self.camera_index = found[0][0]

    def _get_local_subnet(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            parts = ip.split(".")
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        except Exception:
            return "192.168.1.0/24"

    def _scan_ip_cameras(self):
        log_system_action("USER_ACTION", f"กดปุ่มสแกน IP Camera LAN: Subnet {self.ip_range_entry.get().strip()}")
        threading.Thread(target=self._ip_scan_worker, daemon=True).start()

    def _ip_scan_worker(self):
        subnet = self.ip_range_entry.get().strip()
        user = self.cam_user.get().strip()
        password = self.cam_pass.get().strip()
        found = []
        try:
            network = ipaddress.IPv4Network(subnet, strict=False)
            hosts = list(network.hosts())[:15]
            for host in hosts:
                ip = str(host)
                url = f"rtsp://{user}:{password}@{ip}:10554/tcp/av0_0"
                cap = cv2.VideoCapture(url)
                if cap.isOpened():
                    found.append((url, f"RTSP {ip}"))
                    cap.release()
        except Exception:
            pass
        self.root.after(0, lambda: self._update_ip_camera_list(found))

    def _update_ip_camera_list(self, found):
        self.found_ip_cameras = found
        if found:
            self.ip_cam_combo["values"] = [f[1] for f in found]
            self.ip_cam_combo.current(0)
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, found[0][0])
            log_system_action("CONFIG", f"พบ IP Camera และเลือกอัตโนมัติ: {found[0][0]}")

    def _on_ip_cam_select(self, event=None):
        pos = self.ip_cam_combo.current()
        if self.found_ip_cameras and pos < len(self.found_ip_cameras):
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, self.found_ip_cameras[pos][0])
            log_system_action("USER_ACTION", f"เลือก IP Camera จากรายการ: {self.found_ip_cameras[pos][0]}")
            
    def _on_camera_select(self, event=None):
        pos = self.camera_combo.current()
        if self.found_cameras and pos < len(self.found_cameras):
            self.camera_index = self.found_cameras[pos][0]
            log_system_action("USER_ACTION", f"เลือก Webcam: กล้องลำดับที่ {self.camera_index}")

    def reload_app(self):
        log_system_action("USER_ACTION", "กดปุ่ม 🔄 รีโหลดแอปพลิเคชัน")
        self.stop_stream()
        self.result_text.delete("1.0", "end")
        self.sheet_result.delete("1.0", "end")
        self.candidate_code = None
        self.candidate_count = 0
        self.locked_code = None
        self.status.config(text="รีโหลดเรียบร้อย พร้อมตรวจจับใหม่")

    def save_result(self):
        log_system_action("USER_ACTION", "กดปุ่ม 💾 บันทึกผลลัพธ์ลงไฟล์")
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.sheet_result.get("1.0", "end"))
            log_system_action("FILE_SAVE", f"บันทึกผลการทำงานลงไฟล์เรียบร้อย: {path}")
            messagebox.showinfo("สำเร็จ", "บันทึกข้อมูลเรียบร้อย")

    def clear_result(self):
        log_system_action("USER_ACTION", "กดปุ่ม 🗑 ล้างข้อมูลในกล่องข้อความทั้งหมด")
        self.result_text.delete("1.0", "end")
        self.sheet_result.delete("1.0", "end")


if __name__ == "__main__":
    root = tk.Tk()
    app = OCRScannerApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (
        log_system_action("APP_SHUTDOWN", "ปิดโปรแกรม OCRScannerApp ผ่านปุ่มปิดหน้าต่าง"),
        app.stop_stream(), 
        root.destroy()
    ))
    root.mainloop()