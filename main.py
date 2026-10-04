import datetime
import ipaddress
import os
import re
import socket
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import cv2
import numpy as np
from PIL import Image, ImageTk
import requests
from ultralytics import YOLO
import easyocr

# Google Sheet
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
    "ปักเสร็จแล้ว",
    "รอเย็บชุดลูกเสือ",
    "รอปักดาว/จุด",
    "ลูกค้ารับแล้ว",
    "พิมพ์เอง...",
]

STATUS_COLORS = {
    "ปักเสร็จแล้ว": {"red": 0.0, "green": 0.8, "blue": 0.0},
    "รอเย็บชุดลูกเสือ": {"red": 1.0, "green": 0.6, "blue": 0.0},
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

    def connect(self, cred_path="credentials.json"):
        if not GSHEET_AVAILABLE:
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
            return True, f"เชื่อมต่อ '{SHEET_NAME} [{self.sheet.title}]' สำเร็จ (พบ {len(self._cache)} รหัส)"
        except Exception as e:
            return False, str(e)

    def _refresh_cache(self):
        if not self.connected:
            return
        try:
            all_values = self.sheet.get_all_values()
            self._cache = {}
            for row_idx, row in enumerate(all_values, start=1):
                if not row:
                    continue
                code = str(row[0]).strip()
                status = str(row[1]).strip() if len(row) > 1 else ""
                if code:
                    self._cache[code] = (row_idx, status)
        except Exception as e:
            print(f"[cache error] {e}")

    def lookup(self, code):
        code = str(code).strip()
        if code in self._cache:
            return True, self._cache[code][0], self._cache[code][1]
        self._refresh_cache()
        if code in self._cache:
            return True, self._cache[code][0], self._cache[code][1]
        return False, -1, "ไม่พบรหัสนี้ใน Sheet"

    def update_status(self, row_index, new_status, color=None, timestamp="", source=""):
        if not self.connected:
            return False, "ยังไม่ได้เชื่อมต่อ Sheet"
        try:
            self.sheet.update_cell(row_index, 2, new_status)
            if timestamp:
                self.sheet.update_cell(row_index, 7, timestamp)
            if source:
                self.sheet.update_cell(row_index, 5, source)
            base = re.sub(r"\s*\((Auto|Manual)\)\s*$", "", new_status).strip()
            if base == "ลูกค้ารับแล้ว" and timestamp:
                self.sheet.update_cell(row_index, 9, timestamp)
            bg = color if color else {"red": 0.0, "green": 0.8, "blue": 0.0}
            self.sheet.format(f"A{row_index}:B{row_index}", {"backgroundColor": bg})
            # อัปเดต cache ท้องถิ่นให้ตรงกันทันที
            self._refresh_cache()
            return True, f"อัปเดตแถว {row_index} → '{new_status}' แล้ว"
        except Exception as e:
            return False, str(e)

    def notify_line_customer(self, row_index, code, new_status):
        try:
            user_id = str(self.sheet.cell(row_index, 8).value or "").strip()
            if not user_id:
                return
            channel_token = "L7RIVNhGtYj0lNv3iA0ISVllSO8xiv7+OU0ZCB8Yfp+3wlqxfsET0m5HtOtadzKxZf4Fbfwbg2obz1IKElIEyEmeHYl4JHM4rM9PYSz4EnEt92vG5jExY6YMWRAMrap7iKNsOoU/TKz9i3XbynnV6QdB04t89/1O/w1cDnyilFU="
            url = "https://api.line.me/v2/bot/message/push"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {channel_token}",
            }
            msg_text = f"📢 แจ้งเตือนสถานะเสื้อปัก\nรหัส: {code}\nสถานะ: {new_status}"
            payload = {"to": user_id, "messages": [{"type": "text", "text": msg_text}]}
            requests.post(url, headers=headers, json=payload, timeout=5)
        except Exception as e:
            print(f"[LINE Notify Error] {e}")


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
        self.zoom_offset_x = 0.5
        self.zoom_offset_y = 0.5
        self.pan_start = None

        self._undo_stack = []
        self._blink_job = None
        self._indicator_state = "idle"

        # ตัวแปรควบคุมระบบล็อกเป้าและรีเช็ก
        self.candidate_code = None
        self.candidate_count = 0
        self.locked_code = None       # ล็อกรหัสที่ประมวลผลเสร็จแล้ว ไม่ให้อ่านซ้ำจนกว่าจะเปลี่ยนถุง
        self.is_verifying = False

        # โหลดโมเดล
        print("กำลังโหลด YOLO & EasyOCR...")
        self.yolo_model = YOLO("best.pt") if os.path.exists("best.pt") else None
        self.reader = easyocr.Reader(['en'], gpu=False)
        print("✅ โมเดลพร้อมทำงาน")

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
        tk.Label(ctrl, text="| ระบบล็อกเป้าอัตโนมัติ (อ่านครั้งเดียว หยุดรอเปลี่ยนถุงผ้า)", fg="#0055cc", bg="#f0f0f0", font=("Segoe UI", 9, "bold")).pack(side="left", padx=10)

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
        tk.Label(gs_frame, text="สถานะที่จะอัปเดต:", bg="#e8f4e8", font=("Segoe UI", 15, "bold")).pack(side="left", padx=(8, 2))
        self.status_var = tk.StringVar(value=STATUS_OPTIONS[0])
        self.status_combo = ttk.Combobox(gs_frame, textvariable=self.status_var, values=STATUS_OPTIONS, width=30, state="readonly")
        self.status_combo.pack(side="left", padx=4)
        self.status_combo.bind("<<ComboboxSelected>>", self._on_status_select)
        self.custom_status_entry = tk.Entry(gs_frame, width=22, fg="#555")
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

        # Canvas
        self.canvas = tk.Canvas(self.root, bg="black")
        self.canvas.pack(fill="both", expand=True, padx=10, pady=4)
        self.canvas.create_text(400, 200, text="กดปุ่ม '▶ เริ่มตรวจจับสด' เพื่อเปิดกล้อง", fill="white", font=("Segoe UI", 14))

        # Bottom
        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 4))
        left_b = tk.Frame(bottom)
        left_b.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(left_b, text="📝 ล็อกการตรวจจับ (YOLO + EasyOCR):").pack(anchor="w")
        self.result_text = scrolledtext.ScrolledText(left_b, height=6, font=("Consolas", 11))
        self.result_text.pack(fill="both", expand=True)
        right_b = tk.Frame(bottom, width=440)
        right_b.pack(side="left", fill="y")
        right_b.pack_propagate(False)
        tk.Label(right_b, text="📊 บันทึกลง Google Sheet:").pack(anchor="w")
        self.sheet_result = scrolledtext.ScrolledText(right_b, height=6, font=("Segoe UI", 10), bg="#f0fff0")
        self.sheet_result.pack(fill="both", expand=True)

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
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.last_update_label.config(text=f"✅ รหัส {code} → {action}สำเร็จ!", fg="#00ff88")
        self.last_update_time_label.config(text=ts, fg="#aaaaaa")
        self._start_blink("success")

    def _set_indicator_error(self, msg="อัปเดตไม่สำเร็จ"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._start_blink("error")
        self.last_update_label.config(text=f"❌ {msg}", fg="#ff5555")
        self.last_update_time_label.config(text=ts, fg="#aaaaaa")

    # ─────────────────────────── GOOGLE SHEET ───────────────────────────
    def _browse_cred(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            self.cred_path.delete(0, "end")
            self.cred_path.insert(0, path)

    def _connect_sheet(self):
        self.gs_status.config(text="กำลังเชื่อมต่อ...", fg="orange")
        self.root.update()
        ok, msg = self.gs.connect(self.cred_path.get().strip())
        if ok:
            self.gs_status.config(text=f"✅ {msg}", fg="green")
        else:
            self.gs_status.config(text=f"❌ {msg}", fg="red")

    def _lookup_and_update(self, code, source="Auto"):
        if not self.gs.connected:
            self.sheet_result.insert("end", "⚠️ ยังไม่ได้เชื่อมต่อ Sheet\n")
            self.sheet_result.see("end")
            return
        
        base_status = self._get_current_status()
        is_custom_mode = (self.status_var.get() == "พิมพ์เอง...")
        new_status = f"{base_status} (Auto)" if (source == "Auto" and not is_custom_mode) else base_status
        color = self._get_current_color()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        found, row_idx, current_status = self.gs.lookup(code)
        if found:
            # เงื่อนไขเช็คสถานะซ้ำในคอลัมน์ B (ยกเว้นโหมด พิมพ์เอง...)
            if not is_custom_mode:
                current_base = re.sub(r"\s*\((Auto|Manual)\)\s*$", "", current_status).strip()
                if current_base == base_status.strip():
                    line = f"ℹ️ [{ts}] รหัส {code} มีสถานะ '{current_status}' อยู่แล้ว → ข้ามการอัปเดตซ้ำ\n"
                    self.sheet_result.insert("end", line, "skip")
                    self.sheet_result.tag_config("skip", foreground="#0055cc")
                    self.sheet_result.see("end")
                    self._set_indicator_error(f"รหัส {code} มีสถานะนี้อยู่แล้ว")
                    return

            # ถ้าสถานะยังไม่ตรง ให้ทำการอัปเดต
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
            else:
                self.sheet_result.insert("end", f"⚠️ รหัส {code} อัปเดตไม่ได้: {update_msg}\n", "warn")
                self._set_indicator_error()
        else:
            self.sheet_result.insert("end", f"❌ [{ts}] รหัส {code} → ไม่พบในตาราง Sheet\n", "notfound")
            self._set_indicator_error(f"ไม่พบรหัส {code} ใน Sheet")

        self.sheet_result.tag_config("updated", foreground="#1a7340")
        self.sheet_result.tag_config("warn", foreground="orange")
        self.sheet_result.tag_config("notfound", foreground="red")
        self.sheet_result.see("end")

    # ─────────────────────────── STREAM & YOLO TRACKING ───────────────────────────
    def start_stream(self):
        if self.is_streaming:
            return
        src_type = self.source_type.get()
        src = self.camera_index if src_type == "Webcam" else self.url_entry.get().strip()
        self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW if isinstance(src, int) else 0)
        if not self.cap.isOpened():
            messagebox.showerror("Error", f"เปิดกล้องไม่ได้:\n{src}")
            return
        self.is_streaming = True
        threading.Thread(target=self._stream_loop, daemon=True).start()
        self.status.config(text="🟢 กำลังสตรีมภาพและเปิดโหมด YOLO ตรวจจับอัตโนมัติ")

    def stop_stream(self):
        self.is_streaming = False
        if self.cap:
            self.cap.release()
        self.status.config(text="หยุดการทำงานแล้ว")

    def _stream_loop(self):
        frame_counter = 0
        detected_boxes = []

        while self.is_streaming:
            ret, frame = self.cap.read()
            if not ret or frame is None or frame.size == 0:
                time.sleep(0.02)
                continue

            self.current_frame = frame
            display_frame = frame.copy()
            h, w = frame.shape[:2]
            frame_counter += 1

            # วิ่ง YOLO ทุกๆ 3 เฟรม
            if self.yolo_model is not None and frame_counter % 3 == 0:
                results = self.yolo_model(frame, conf=0.35, verbose=False)
                new_boxes = []
                for r in results:
                    for box in r.boxes:
                        bx1, by1, bx2, by2 = map(int, box.xyxy[0])
                        new_boxes.append((bx1, by1, bx2, by2))
                detected_boxes = new_boxes

                # ถ้าเจอวัตถุ และยังไม่ได้ล็อกรหัสนี้ ค่อยส่งไปตรวจ OCR Double-Check
                if detected_boxes and not self.is_verifying:
                    bx1, by1, bx2, by2 = detected_boxes[0]
                    pad = 8
                    cx1 = max(0, bx1 - pad)
                    cy1 = max(0, by1 - pad)
                    cx2 = min(w, bx2 + pad)
                    cy2 = min(h, by2 + pad)
                    crop_target = frame[cy1:cy2, cx1:cx2]

                    if crop_target.size > 0:
                        threading.Thread(target=self._verify_and_process, args=(crop_target,), daemon=True).start()

            # วาดกรอบสี่เหลี่ยมบนหน้าจอ
            for (bx1, by1, bx2, by2) in detected_boxes:
                # ถ้าล็อกรหัสนี้ไปแล้ว กรอบจะเป็นสีฟ้า (แสดงว่าประมวลผลเสร็จแล้ว รอเปลี่ยนถุง)
                box_color = (255, 0, 0) if self.locked_code else (0, 255, 0)
                cv2.rectangle(display_frame, (bx1, by1), (bx2, by2), box_color, 3)
                
                label_text = f"Locked: {self.locked_code}" if self.locked_code else f"Detect: {self.candidate_code if self.candidate_code else 'Scanning...'}"
                cv2.putText(display_frame, label_text, (bx1, max(25, by1 - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, box_color, 2)

            self._update_canvas(display_frame)
            time.sleep(0.01)

    def _verify_and_process(self, crop_img):
        self.is_verifying = True
        try:
            ocr_res = self.reader.readtext(crop_img, allowlist='0123456789')
            found_4digit = None
            for _, text, prob in ocr_res:
                clean = text.strip()
                if re.fullmatch(r'\d{4}', clean) and prob > 0.4:
                    found_4digit = clean
                    break

            if found_4digit:
                # ถ้าเจอรหัสนี้ตรงกับรหัสที่ล็อกไว้ก่อนหน้า ให้ข้ามการอ่านไปเลย (จนกว่าจะเอาถุงออก)
                if found_4digit == self.locked_code:
                    self.is_verifying = False
                    return

                # รีเซ็ตตัวล็อกถ้าเปลี่ยนไปเจอถุงใหม่
                if self.locked_code and found_4digit != self.locked_code:
                    self.locked_code = None

                # ระบบรีเช็ก Double-Check 2 รอบ
                if self.candidate_code == found_4digit:
                    self.candidate_count += 1
                else:
                    self.candidate_code = found_4digit
                    self.candidate_count = 1

                log = f"[Detect] พบตัวเลข {found_4digit} (เช็กครั้งที่ {self.candidate_count}/2)\n"
                self.root.after(0, lambda: self.result_text.insert("end", log))
                self.root.after(0, lambda: self.result_text.see("end"))

                # คอนเฟิร์มครบ 2 รอบ
                if self.candidate_count >= 2:
                    self.locked_code = found_4digit  # ล็อกรหัสนี้ทันทีเพื่อหยุดอ่านซ้ำ
                    
                    confirm_log = f"🎯 [CONFIRMED] ยืนยันรหัส {found_4digit} ครบ 2 รอบ → ตรวจสอบสถานะและอัปเดต Sheet!\n{'-'*40}\n"
                    self.root.after(0, lambda: self.result_text.insert("end", confirm_log))
                    self.root.after(0, lambda: self.result_text.see("end"))
                    self.root.after(0, lambda c=found_4digit: self._lookup_and_update(c, source="Auto"))

                    self.candidate_code = None
                    self.candidate_count = 0
            else:
                self.candidate_count = 0
        except Exception as e:
            print(f"[Verify Error] {e}")
        finally:
            self.is_verifying = False

    def _update_canvas(self, frame):
        try:
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 400
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            fh, fw = rgb.shape[:2]
            scale = min(cw / fw, ch / fh)
            dw, dh = int(fw * scale), int(fh * scale)
            resized = cv2.resize(rgb, (dw, dh), interpolation=cv2.INTER_NEAREST)
            img = ImageTk.PhotoImage(Image.fromarray(resized))
            self.root.after(0, lambda i=img: self._show_canvas(i))
        except Exception:
            pass

    def _show_canvas(self, img):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=img)
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
        threading.Thread(target=self._undo_worker, args=(entry,), daemon=True).start()

    def _undo_worker(self, entry):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old_color = entry.get("old_color", STATUS_COLORS["_custom"])
        ok, msg = self.gs.update_status(entry["row_idx"], entry["old_status"], old_color, timestamp=ts)
        if ok:
            line = f"↩ [{ts}] Undo: รหัส {entry['code']} กลับเป็น '{entry['old_status']}'\n"
            self.root.after(0, lambda: self.sheet_result.insert("end", line, "updated"))
            self.root.after(0, lambda: self._set_indicator_success(entry['code'], action="ย้อนกลับ"))
            self.root.after(0, self._update_undo_ui)

    def _zoom_in(self):
        self.zoom_level = min(self.zoom_max, round(self.zoom_level + self.zoom_step, 2))
        self.zoom_label.config(text=f"{self.zoom_level:.1f}x")

    def _zoom_out(self):
        self.zoom_level = max(self.zoom_min, round(self.zoom_level - self.zoom_step, 2))
        self.zoom_label.config(text=f"{self.zoom_level:.1f}x")

    def _zoom_reset(self):
        self.zoom_level = 1.0
        self.zoom_label.config(text="1.0x")

    def _on_source_change(self, event=None):
        src = self.source_type.get()
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
        if self.status_var.get() == "พิมพ์เอง...":
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

    def _on_camera_select(self, event=None):
        pos = self.camera_combo.current()
        if self.found_cameras and pos < len(self.found_cameras):
            self.camera_index = self.found_cameras[pos][0]

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

    def _on_ip_cam_select(self, event=None):
        pos = self.ip_cam_combo.current()
        if self.found_ip_cameras and pos < len(self.found_ip_cameras):
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, self.found_ip_cameras[pos][0])

    def reload_app(self):
        self.stop_stream()
        self.result_text.delete("1.0", "end")
        self.sheet_result.delete("1.0", "end")
        self.candidate_code = None
        self.candidate_count = 0
        self.locked_code = None
        self.status.config(text="รีโหลดเรียบร้อย พร้อมตรวจจับใหม่")

    def save_result(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.sheet_result.get("1.0", "end"))
            messagebox.showinfo("สำเร็จ", "บันทึกข้อมูลเรียบร้อย")

    def clear_result(self):
        self.result_text.delete("1.0", "end")
        self.sheet_result.delete("1.0", "end")


if __name__ == "__main__":
    root = tk.Tk()
    app = OCRScannerApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_stream(), root.destroy()))
    root.mainloop()