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
import pytesseract

# Google Sheet
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    GSHEET_AVAILABLE = True
except ImportError:
    GSHEET_AVAILABLE = False

if os.path.exists("Tesseract-OCR/tesseract.exe"):
    pytesseract.pytesseract.tesseract_cmd = os.path.abspath(
        "Tesseract-OCR/tesseract.exe"
    )
else:
    pytesseract.pytesseract.tesseract_cmd = (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )

RTSP_PATHS = [
    "/tcp/av0_0",  # TONG BO WEI
    "/live/ch0",  # Guangzhou Juan / EseeCloud
    "/onvif1",  # Onvif / iComm Semiconductor
    "/onvif2",  # Onvif สำรอง
    "/stream1",  # Tapo / ทั่วไป
    "/cam/realmonitor?channel=1&subtype=0",  # Dahua / IMOU
    "/h264/ch1/main/av_stream",  # Hikvision
    "/live",
    "",
]
HTTP_PATHS = ["/video", "/mjpeg", "/stream", "/?action=stream"]

COMMON_PORTS = [554, 10554, 8554, 8080, 80, 8081, 1935]
SHEET_NAME = "VSearch4.0"

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
            return (
                False,
                "ไม่พบ gspread ติดตั้งด้วย: pip install gspread oauth2client",
            )
        try:
            scopes = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                cred_path, scopes
            )
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open(SHEET_NAME).sheet1
            self.connected = True
            self._refresh_cache()
            return (
                True,
                f"เชื่อมต่อ '{SHEET_NAME}' สำเร็จ (พบ {len(self._cache)} รหัส)",
            )
        except FileNotFoundError:
            return False, "ไม่พบไฟล์ credentials.json"
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

    def update_status(
        self, row_index, new_status, color=None, timestamp="", source=""
    ):
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
                self.sheet.update_cell(row_index, 8, timestamp)
            bg = color if color else {"red": 0.0, "green": 0.8, "blue": 0.0}
            self.sheet.format(
                f"A{row_index}:B{row_index}", {"backgroundColor": bg}
            )
            return True, f"อัปเดตแถว {row_index} → '{new_status}' แล้ว"
        except Exception as e:
            return False, str(e)


class OCRScannerApp:

    def __init__(self, root):
        self.root = root
        self.root.title("OCR Document Scanner + Google Sheet")
        self.root.geometry("1400x960")
        self.current_frame = None
        self.is_streaming = False
        self.cap = None
        self.camera_index = 2
        self.found_cameras = []
        self.found_ip_cameras = []
        self.crop = {"x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9}
        self.drag_start = None
        self.drag_mode = None
        self.auto_capture = False
        self.auto_interval = 5
        self._auto_event = None
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

        self._build_ui()
        self._scan_cameras()

    # ─────────────────────────── AUTO CAPTURE ───────────────────────────
    def _toggle_auto(self):
        self.auto_capture = self.auto_var.get()
        if self.auto_capture:
            try:
                self.auto_interval = int(self.interval_var.get())
            except ValueError:
                self.auto_interval = 5
            self._auto_event = threading.Event()
            threading.Thread(target=self._auto_loop, daemon=True).start()
            self.status.config(
                text=f"จับภาพอัตโนมัติทุก {self.auto_interval} วินาที"
            )

    def _auto_loop(self):
        while self.auto_capture and self.is_streaming:
            self._auto_event.wait(timeout=self.auto_interval)
            self._auto_event.clear()
            if self.auto_capture and self.current_frame is not None:
                threading.Thread(
                    target=self._do_ocr,
                    args=(self.current_frame.copy(),),
                    daemon=True,
                ).start()

    # ─────────────────────────── UI ───────────────────────────
    def _build_ui(self):
        top = tk.Frame(self.root, pady=6)
        top.pack(fill="x", padx=10)

        tk.Label(top, text="แหล่งกล้อง:").pack(side="left")
        self.source_type = ttk.Combobox(
            top,
            values=["Webcam", "IP Camera (LAN)", "URL โดยตรง"],
            width=18,
            state="readonly",
        )
        self.source_type.current(0)
        self.source_type.pack(side="left", padx=4)
        self.source_type.bind("<<ComboboxSelected>>", self._on_source_change)

        self.url_frame = tk.Frame(top)
        self.url_entry = tk.Entry(self.url_frame, width=42)
        self.url_entry.insert(
            0, "rtsp://admin:Sitthik0rn@192.168.1.111:10554/tcp/av0_0"
        )
        self.url_entry.pack(side="left")
        tk.Button(
            self.url_frame,
            text="🔗 เชื่อมต่อ",
            bg="#0078d4",
            fg="white",
            command=self.start_stream,
        ).pack(side="left", padx=2)

        for text, color, cmd in [
            ("▶ เริ่ม", "green", self.start_stream),
            ("⏹ หยุด", "red", self.stop_stream),
            ("📸 จับภาพ+OCR", "#0055cc", self.capture_and_ocr),
            ("📂 เปิดไฟล์", "#555", self.open_file),
            ("💾 บันทึก", "#2a7a2a", self.save_result),
            ("🗑 ล้าง", "#888", self.clear_result),
            ("🔄 รีโหลด", "#8B4513", self.reload_app),
        ]:
            tk.Button(top, text=text, bg=color, fg="white", command=cmd).pack(
                side="left", padx=2
            )

        # Row 2: webcam
        self.row_webcam = tk.Frame(self.root, pady=2)
        self.row_webcam.pack(fill="x", padx=10)
        tk.Label(self.row_webcam, text="เลือกกล้อง:").pack(side="left")
        self.camera_combo = ttk.Combobox(
            self.row_webcam, values=[], width=28, state="readonly"
        )
        self.camera_combo.pack(side="left", padx=4)
        self.camera_combo.bind("<<ComboboxSelected>>", self._on_camera_select)
        tk.Button(
            self.row_webcam, text="🔍 ค้นหากล้อง", command=self._scan_cameras
        ).pack(side="left", padx=4)

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
        tk.Button(
            self.row_ip,
            text="🌐 สแกน LAN",
            bg="#7b2d8b",
            fg="white",
            command=self._scan_ip_cameras,
        ).pack(side="left", padx=4)
        self.scan_progress = tk.Label(self.row_ip, text="", fg="#0078d4")
        self.scan_progress.pack(side="left", padx=4)
        self.ip_cam_combo = ttk.Combobox(
            self.row_ip, values=[], width=48, state="readonly"
        )
        self.ip_cam_combo.pack(side="left", padx=4)
        self.ip_cam_combo.bind("<<ComboboxSelected>>", self._on_ip_cam_select)

        # Row 4: Crop + Zoom
        ctrl = tk.Frame(self.root, pady=3, bg="#f0f0f0")
        ctrl.pack(fill="x", padx=10)

        self.auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            ctrl,
            text="จับภาพอัตโนมัติ",
            variable=self.auto_var,
            command=self._toggle_auto,
            bg="#f0f0f0",
        ).pack(side="left")
        tk.Label(ctrl, text="ทุก", bg="#f0f0f0").pack(side="left", padx=(6, 2))
        self.interval_var = tk.StringVar(value="5")
        tk.Spinbox(
            ctrl, from_=1, to=60, width=4, textvariable=self.interval_var
        ).pack(side="left")
        tk.Label(ctrl, text="วินาที  |", bg="#f0f0f0").pack(
            side="left", padx=(2, 8)
        )
        tk.Label(
            ctrl,
            text="🔲 ลากกรอบ crop |",
            fg="#555",
            bg="#f0f0f0",
            font=("Segoe UI", 9, "italic"),
        ).pack(side="left")
        tk.Button(ctrl, text="รีเซ็ต crop", command=self._reset_crop).pack(
            side="left", padx=4
        )
        tk.Label(ctrl, text="  🔍 ซูม:", bg="#f0f0f0").pack(
            side="left", padx=(8, 2)
        )
        tk.Button(
            ctrl, text="➕", width=3, command=self._zoom_in, bg="#ddd"
        ).pack(side="left", padx=1)
        self.zoom_label = tk.Label(
            ctrl,
            text="1.0x",
            width=5,
            bg="#f0f0f0",
            font=("Segoe UI", 9, "bold"),
        )
        self.zoom_label.pack(side="left")
        tk.Button(
            ctrl, text="➖", width=3, command=self._zoom_out, bg="#ddd"
        ).pack(side="left", padx=1)
        tk.Button(ctrl, text="รีเซ็ต", command=self._zoom_reset, bg="#ddd").pack(
            side="left", padx=4
        )
        tk.Label(
            ctrl,
            text="| Ctrl+ลาก = เลื่อนภาพ",
            fg="#888",
            bg="#f0f0f0",
            font=("Segoe UI", 8, "italic"),
        ).pack(side="left")

        # Row 5: Google Sheet
        gs_frame = tk.Frame(self.root, pady=3, bg="#e8f4e8")
        gs_frame.pack(fill="x", padx=10)
        tk.Label(
            gs_frame,
            text="🟢 Google Sheet:",
            font=("Segoe UI", 9, "bold"),
            bg="#e8f4e8",
        ).pack(side="left")
        tk.Label(gs_frame, text="credentials.json:", bg="#e8f4e8").pack(
            side="left", padx=(8, 2)
        )
        self.cred_path = tk.Entry(gs_frame, width=24)
        self.cred_path.insert(0, "credentials.json")
        self.cred_path.pack(side="left", padx=2)
        tk.Button(gs_frame, text="📁", command=self._browse_cred).pack(
            side="left"
        )
        tk.Button(
            gs_frame,
            text="🔌 เชื่อมต่อ Sheet",
            bg="#1a7340",
            fg="white",
            command=self._connect_sheet,
        ).pack(side="left", padx=6)
        tk.Label(
            gs_frame,
            text="สถานะที่จะอัปเดต:",
            bg="#e8f4e8",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(8, 2))
        self.status_var = tk.StringVar(value=STATUS_OPTIONS[0])
        self.status_combo = ttk.Combobox(
            gs_frame,
            textvariable=self.status_var,
            values=STATUS_OPTIONS,
            width=22,
            state="readonly",
        )
        self.status_combo.pack(side="left", padx=4)
        self.status_combo.bind("<<ComboboxSelected>>", self._on_status_select)
        self.custom_status_entry = tk.Entry(gs_frame, width=22, fg="#555")
        self.custom_status_entry.insert(0, "พิมพ์สถานะที่ต้องการ...")
        self.gs_status = tk.Label(
            gs_frame,
            text="ยังไม่ได้เชื่อมต่อ",
            fg="gray",
            bg="#e8f4e8",
            font=("Segoe UI", 9),
        )
        self.gs_status.pack(side="left", padx=6)

        # Row 6: Manual code entry + Indicator + Undo
        manual_frame = tk.Frame(
            self.root, pady=4, bg="#eef4ff", relief="groove", bd=1
        )
        manual_frame.pack(fill="x", padx=10, pady=(0, 0))

        tk.Label(
            manual_frame,
            text="✏️ ป้อนรหัส 1-4 หลัก:",
            font=("Segoe UI", 9, "bold"),
            bg="#eef4ff",
        ).pack(side="left", padx=(8, 4))
        self.manual_code_entry = tk.Entry(
            manual_frame,
            width=10,
            font=("Segoe UI", 11, "bold"),
            justify="center",
        )
        self.manual_code_entry.pack(side="left", padx=4)
        self.manual_code_entry.bind("<Return>", self._on_manual_code_enter)
        self.manual_code_entry.bind("<KeyRelease>", self._limit_manual_entry)

        tk.Button(
            manual_frame,
            text="✅ อัปเดต",
            bg="#1a7340",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            command=self._on_manual_code_enter,
        ).pack(side="left", padx=4)
        tk.Label(manual_frame, text="|", bg="#eef4ff", fg="#aaa").pack(
            side="left", padx=6
        )

        # Status indicator canvas
        self.indicator_canvas = tk.Canvas(
            manual_frame, width=32, height=32, bg="#eef4ff", highlightthickness=0
        )
        self.indicator_canvas.pack(side="left", padx=4)
        self._draw_indicator("idle")

        tk.Label(manual_frame, text="|", bg="#eef4ff", fg="#aaa").pack(
            side="left", padx=6
        )

        # Undo button
        self.undo_btn = tk.Button(
            manual_frame,
            text="↩ Undo",
            bg="#c0392b",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            state="disabled",
            command=self._undo_last,
        )
        self.undo_btn.pack(side="left", padx=4)
        self.undo_info_label = tk.Label(
            manual_frame,
            text="",
            font=("Segoe UI", 8),
            bg="#eef4ff",
            fg="#888",
        )
        self.undo_info_label.pack(side="left", padx=4)

        # Row 7: Last Update Banner
        last_update_frame = tk.Frame(self.root, bg="#1a1a2e", pady=6)
        last_update_frame.pack(fill="x", padx=10, pady=(0, 2))

        tk.Label(
            last_update_frame,
            text="LAST UPDATE",
            font=("Segoe UI", 9, "bold"),
            bg="#1a1a2e",
            fg="#7faaff",
        ).pack(side="left", padx=(12, 6))
        self.last_update_label = tk.Label(
            last_update_frame,
            text="—  รอการอัปเดต",
            font=("Segoe UI", 16, "bold"),
            bg="#1a1a2e",
            fg="#ffffff",
            anchor="w",
        )
        self.last_update_label.pack(side="left", padx=4, fill="x", expand=True)

        self.last_update_time_label = tk.Label(
            last_update_frame,
            text="",
            font=("Segoe UI", 10),
            bg="#1a1a2e",
            fg="#aaaaaa",
            anchor="e",
        )
        self.last_update_time_label.pack(side="right", padx=12)

        # Canvas
        self.canvas = tk.Canvas(self.root, bg="black", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=10, pady=4)
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_press)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.create_text(
            400,
            200,
            text="ยังไม่ได้เชื่อมต่อ",
            fill="white",
            font=("Segoe UI", 14),
        )

        # Bottom
        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 4))
        left_b = tk.Frame(bottom)
        left_b.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(left_b, text="📝 ผลลัพธ์ OCR:").pack(anchor="w")
        self.result_text = scrolledtext.ScrolledText(
            left_b, height=6, font=("TH Sarabun New", 12)
        )
        self.result_text.pack(fill="both", expand=True)
        right_b = tk.Frame(bottom, width=440)
        right_b.pack(side="left", fill="y")
        right_b.pack_propagate(False)
        tk.Label(right_b, text="📊 ผลลัพธ์ Google Sheet:").pack(anchor="w")
        self.sheet_result = scrolledtext.ScrolledText(
            right_b, height=6, font=("TH Sarabun New", 12), bg="#f0fff0"
        )
        self.sheet_result.pack(fill="both", expand=True)

        self.status = tk.Label(
            self.root, text="พร้อมใช้งาน", fg="green", anchor="w"
        )
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
        self.indicator_canvas.create_oval(
            4, 4, 24, 24, fill=fill, outline=outline, width=2
        )

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
        self._blink_job = self.root.after(
            300, lambda: self._blink_cycle(state, not on)
        )

    def _set_indicator_success(self, codes, action="อัปเดต"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        codes_str = "  ,  ".join(str(c) for c in codes)
        self.last_update_label.config(
            text=f"✅  รหัส  {codes_str}  →  {action}สำเร็จ {ts}", fg="#00ff88"
        )
        self.last_update_time_label.config(text=ts, fg="#aaaaaa")
        self._start_blink("success")

    def _set_indicator_error(self, msg="อัปเดตไม่สำเร็จ"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._start_blink("error")
        self.last_update_label.config(text=f"❌  {msg}", fg="#ff5555")
        self.last_update_time_label.config(text=ts, fg="#aaaaaa")

    # ─────────────────────────── MANUAL ENTRY ───────────────────────────
    def _limit_manual_entry(self, event=None):
        val = self.manual_code_entry.get()
        cleaned = re.sub(r"[^0-9]", "", val)[:4]
        if cleaned != val:
            self.manual_code_entry.delete(0, "end")
            self.manual_code_entry.insert(0, cleaned)

    def _on_manual_code_enter(self, event=None):
        code = self.manual_code_entry.get().strip()
        if not re.fullmatch(r"\d{1,4}", code):
            messagebox.showwarning(
                "รหัสไม่ถูกต้อง", "กรุณาพิมพ์ตัวเลข 1-4 หลัก เช่น 5, 23, 1234"
            )
            return
        if not self.gs.connected:
            messagebox.showwarning(
                "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อ Google Sheet ก่อน"
            )
            self._set_indicator_error()
            return
        threading.Thread(
            target=self._manual_update_worker, args=(code,), daemon=True
        ).start()

    def _manual_update_worker(self, code):
        base_status = self._get_current_status()
        new_status = self._get_current_status()
        color = self._get_current_color()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        found, row_idx, current_status = self.gs.lookup(code)
        if not found:
            line = f"\n[{ts}] ✏️ Manual\n❌ รหัส {code} → ไม่พบใน Sheet\n"
            self.root.after(
                0, lambda: self.sheet_result.insert("end", line, "notfound")
            )
            self.root.after(0, lambda: self.sheet_result.see("end"))
            self.root.after(0, self._set_indicator_error)
            return

        current_base = re.sub(
            r"\s*\((Auto|Manual)\)\s*$", "", current_status
        ).strip()
        if current_base == base_status.strip():
            line = f"\n[{ts}] ✏️ Manual\nℹ️ รหัส {code} มีสถานะ '{current_status}' อยู่แล้ว\n"
            self.root.after(
                0, lambda: self.sheet_result.insert("end", line, "skip")
            )
            self.root.after(0, lambda: self.sheet_result.see("end"))
            self.root.after(
                0,
                lambda: self._set_indicator_error(
                    f"รหัส {code} มีสถานะนี้อยู่แล้ว"
                ),
            )
            return

        ok, update_msg = self.gs.update_status(
            row_idx, new_status, color, timestamp=ts, source="Manual"
        )
        if ok:
            self._undo_stack.append(
                {
                    "code": code,
                    "row_idx": row_idx,
                    "old_status": current_status,
                    "new_status": new_status,
                    "old_color": self._status_to_color(current_status),
                }
            )
            line = f"\n[{ts}] ✏️ Manual\n✅ รหัส {code} (แถว {row_idx})\n   {current_status} → {new_status}\n"
            self.root.after(
                0, lambda: self.sheet_result.insert("end", line, "updated")
            )
            self.root.after(0, lambda: self.sheet_result.see("end"))
            self.root.after(0, lambda: self._set_indicator_success([code]))
            self.root.after(0, self._update_undo_ui)
            self.root.after(0, lambda: self.manual_code_entry.delete(0, "end"))
        else:
            line = f"\n[{ts}] ✏️ Manual\n⚠️ รหัส {code} อัปเดตไม่สำเร็จ: {update_msg}\n"
            self.root.after(
                0, lambda: self.sheet_result.insert("end", line, "warn")
            )
            self.root.after(0, lambda: self.sheet_result.see("end"))
            self.root.after(0, self._set_indicator_error)

        self.root.after(
            0, lambda: self.sheet_result.tag_config("updated", foreground="#1a7340")
        )
        self.root.after(
            0, lambda: self.sheet_result.tag_config("skip", foreground="#0055cc")
        )
        self.root.after(
            0, lambda: self.sheet_result.tag_config("warn", foreground="orange")
        )
        self.root.after(
            0, lambda: self.sheet_result.tag_config("notfound", foreground="red")
        )

    def _status_to_color(self, status_text):
        return STATUS_COLORS.get(status_text, STATUS_COLORS["_custom"])

    # ─────────────────────────── UNDO ───────────────────────────
    def _update_undo_ui(self):
        if self._undo_stack:
            last = self._undo_stack[-1]
            self.undo_btn.config(state="normal")
            self.undo_info_label.config(
                text=f"← รหัส {last['code']}: '{last['new_status']}' → '{last['old_status']}'"
            )
        else:
            self.undo_btn.config(state="disabled")
            self.undo_info_label.config(text="")

    def _undo_last(self):
        if not self._undo_stack:
            return
        if not self.gs.connected:
            messagebox.showwarning(
                "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อ Google Sheet ก่อน"
            )
            return
        entry = self._undo_stack[-1]
        threading.Thread(
            target=self._undo_worker, args=(entry,), daemon=True
        ).start()

    def _undo_worker(self, entry):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old_color = entry.get("old_color", STATUS_COLORS["_custom"])
        ok, msg = self.gs.update_status(
            entry["row_idx"], entry["old_status"], old_color, timestamp=ts
        )
        if ok:
            self._undo_stack.pop()
            self.auto_capture = False
            self.auto_var.set(False)
            line = (
                f"\n[{ts}] ↩ Undo\n"
                f"✅ รหัส {entry['code']} (แถว {entry['row_idx']})\n"
                f"   {entry['new_status']} → {entry['old_status']} (ย้อนกลับ)\n"
            )
            self.root.after(
                0, lambda: self.sheet_result.insert("end", line, "updated")
            )
            self.root.after(0, lambda: self.sheet_result.see("end"))
            self.root.after(
                0,
                lambda: self._set_indicator_success(
                    [entry["code"]], action="ย้อนกลับ"
                ),
            )
            self.root.after(0, self._update_undo_ui)
        else:
            line = f"\n[{ts}] ↩ Undo ล้มเหลว: {msg}\n"
            self.root.after(
                0, lambda: self.sheet_result.insert("end", line, "warn")
            )
            self.root.after(0, lambda: self.sheet_result.see("end"))
            self.root.after(0, self._set_indicator_error)
        self.root.after(
            0, lambda: self.sheet_result.tag_config("updated", foreground="#1a7340")
        )
        self.root.after(
            0, lambda: self.sheet_result.tag_config("warn", foreground="orange")
        )

    # ─────────────────────────── RELOAD ───────────────────────────
    def reload_app(self):
        self.is_streaming = False
        self.auto_capture = False
        self.auto_var.set(False)
        time.sleep(0.1)
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

        self.current_frame = None
        self.found_cameras = []
        self.found_ip_cameras = []
        self.zoom_level = 1.0
        self.zoom_offset_x = 0.5
        self.zoom_offset_y = 0.5
        self.zoom_label.config(text="1.0x")
        self.crop = {"x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9}
        self.drag_mode = None
        self.drag_start = None
        self.pan_start = None

        self.result_text.delete("1.0", "end")
        self.sheet_result.delete("1.0", "end")

        self.gs = GoogleSheetManager()
        self.gs_status.config(text="ยังไม่ได้เชื่อมต่อ", fg="gray")

        self._undo_stack.clear()
        self._update_undo_ui()
        self._draw_indicator("idle")
        self.last_update_label.config(text="—  รอการอัปเดต", fg="#ffffff")
        self.last_update_time_label.config(text="")
        self.manual_code_entry.delete(0, "end")
        if self._blink_job:
            self.root.after_cancel(self._blink_job)
            self._blink_job = None

        self.canvas.delete("all")
        self.canvas.create_text(
            400,
            200,
            text="🔄 รีโหลดแล้ว กรุณาเริ่มสตรีมใหม่",
            fill="white",
            font=("Segoe UI", 14),
        )

        self.status.config(text="🔄 รีโหลดเสร็จสิ้น กำลังค้นหากล้อง...")
        self.root.after(500, self._scan_cameras)

    # ─────────────────────────── SOURCE CHANGE ───────────────────────────
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

    # ─────────────────────────── STATUS ───────────────────────────
    def _on_status_select(self, event=None):
        if self.status_var.get() == "พิมพ์เอง...":
            self.custom_status_entry.pack(side="left", padx=4)
            self.custom_status_entry.focus()
        else:
            self.custom_status_entry.pack_forget()

    def _get_current_status(self):
        selected = self.status_var.get()
        if selected == "พิมพ์เอง...":
            custom = self.custom_status_entry.get().strip()
            return (
                custom
                if custom and custom != "พิมพ์สถานะที่ต้องการ..."
                else "ปักเสร็จแล้ว"
            )
        return selected

    def _get_current_color(self):
        selected = self.status_var.get()
        if selected == "พิมพ์เอง...":
            return STATUS_COLORS["_custom"]
        return STATUS_COLORS.get(selected, STATUS_COLORS["_custom"])

    # ─────────────────────────── ZOOM & PAN ───────────────────────────
    def _zoom_in(self):
        self.zoom_level = min(
            self.zoom_max, round(self.zoom_level + self.zoom_step, 2)
        )
        self.zoom_label.config(text=f"{self.zoom_level:.1f}x")

    def _zoom_out(self):
        self.zoom_level = max(
            self.zoom_min, round(self.zoom_level - self.zoom_step, 2)
        )
        self.zoom_label.config(text=f"{self.zoom_level:.1f}x")
        if self.zoom_level == self.zoom_min:
            self.zoom_offset_x = 0.5
            self.zoom_offset_y = 0.5

    def _zoom_reset(self):
        self.zoom_level = 1.0
        self.zoom_offset_x = 0.5
        self.zoom_offset_y = 0.5
        self.zoom_label.config(text="1.0x")

    def _on_mousewheel(self, event):
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _apply_zoom(self, frame):
        if self.zoom_level <= 1.0:
            return frame
        h, w = frame.shape[:2]
        crop_w = int(w / self.zoom_level)
        crop_h = int(h / self.zoom_level)
        cx = int(self.zoom_offset_x * w)
        cy = int(self.zoom_offset_y * h)
        x1 = max(0, min(cx - crop_w // 2, w - crop_w))
        y1 = max(0, min(cy - crop_h // 2, h - crop_h))
        x2 = x1 + crop_w
        y2 = y1 + crop_h
        cropped = frame[y1:y2, x1:x2]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    # ─────────────────────────── MOUSE ───────────────────────────
    def _on_mouse_press(self, e):
        ctrl_held = (e.state & 0x4) != 0
        if ctrl_held and self.zoom_level > 1.0:
            self.drag_mode = "pan"
            self.pan_start = (e.x, e.y, self.zoom_offset_x, self.zoom_offset_y)
        else:
            self.drag_mode = "crop"
            cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
            self.drag_start = (e.x / max(1, cw), e.y / max(1, ch))

    def _on_mouse_drag(self, e):
        if self.drag_mode == "pan" and self.pan_start:
            px, py, ox, oy = self.pan_start
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 400
            dx = (e.x - px) / cw / self.zoom_level
            dy = (e.y - py) / ch / self.zoom_level
            self.zoom_offset_x = max(0.0, min(1.0, ox - dx))
            self.zoom_offset_y = max(0.0, min(1.0, oy - dy))
        elif self.drag_mode == "crop" and self.drag_start:
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 400
            x1 = min(self.drag_start[0], e.x / cw)
            y1 = min(self.drag_start[1], e.y / ch)
            x2 = max(self.drag_start[0], e.x / cw)
            y2 = max(self.drag_start[1], e.y / ch)
            self.crop = {
                "x1": max(0, x1),
                "y1": max(0, y1),
                "x2": min(1, x2),
                "y2": min(1, y2),
            }

    def _on_mouse_release(self, e):
        self.drag_mode = None
        self.drag_start = None
        self.pan_start = None

    def _reset_crop(self):
        self.crop = {"x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9}

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

    def _lookup_and_update(self, codes, source="auto"):
        if not self.gs.connected:
            self.sheet_result.insert("end", "⚠️ ยังไม่ได้เชื่อมต่อ Sheet\n")
            self.sheet_result.see("end")
            return
        base_status = self._get_current_status()
        new_status = (
            f"{base_status} (Auto)" if source == "auto" else base_status
        )
        color = self._get_current_color()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.sheet_result.insert("end", f"\n[{ts}] สถานะ: {new_status}\n")
        updated_codes = []
        skipped_codes = []
        any_error = False
        for code in codes:
            found, row_idx, current_status = self.gs.lookup(code)
            if found:
                current_base = re.sub(
                    r"\s*\((Auto|Manual)\)\s*$", "", current_status
                ).strip()
                if current_base == base_status.strip():
                    line = f"ℹ️ รหัส {code} (แถว {row_idx}) มีสถานะ '{current_status}' อยู่แล้ว — ไม่มีการเปลี่ยนแปลง\n"
                    self.sheet_result.insert("end", line, "skip")
                    skipped_codes.append(code)
                else:
                    ok, update_msg = self.gs.update_status(
                        row_idx,
                        new_status,
                        color,
                        timestamp=ts,
                        source="Auto",
                    )
                    if ok:
                        self._undo_stack.append(
                            {
                                "code": code,
                                "row_idx": row_idx,
                                "old_status": current_status,
                                "new_status": new_status,
                                "old_color": self._status_to_color(
                                    current_status
                                ),
                            }
                        )
                        line = f"✅ รหัส {code} (แถว {row_idx})\n   {current_status} → {new_status}\n   📅 col G: {ts}\n"
                        self.sheet_result.insert("end", line, "updated")
                        updated_codes.append(code)
                    else:
                        line = f"⚠️ รหัส {code} พบแต่อัปเดตไม่ได้: {update_msg}\n"
                        self.sheet_result.insert("end", line, "warn")
                        any_error = True
            else:
                line = f"❌ รหัส {code} → ไม่พบใน Sheet\n"
                self.sheet_result.insert("end", line, "notfound")
                any_error = True

        self.sheet_result.tag_config("updated", foreground="#1a7340")
        self.sheet_result.tag_config("skip", foreground="#0055cc")
        self.sheet_result.tag_config("warn", foreground="orange")
        self.sheet_result.tag_config("notfound", foreground="red")
        self.sheet_result.see("end")

        if updated_codes:
            self._set_indicator_success(updated_codes)
        elif skipped_codes and not any_error:
            self._set_indicator_error(
                f"รหัส {', '.join(skipped_codes)} มีสถานะเดิมอยู่แล้ว"
            )
        else:
            self._set_indicator_error()

        self._update_undo_ui()

    # ─────────────────────────── WEBCAM SCAN ───────────────────────────
    def _scan_cameras(self):
        self.status.config(text="กำลังค้นหากล้อง...")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        found = []
        for i in range(6):
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
            labels = [f[1] for f in found]
            self.camera_combo["values"] = labels
            self.camera_combo.current(0)
            self.camera_index = found[0][0]
            self.status.config(
                text=f"พบกล้อง {len(found)} ตัว: {', '.join(labels)}"
            )
        else:
            self.camera_combo["values"] = ["ไม่พบกล้อง"]
            self.camera_combo.current(0)
            self.status.config(text="ไม่พบกล้อง")

    def _on_camera_select(self, event=None):
        pos = self.camera_combo.current()
        if self.found_cameras and pos < len(self.found_cameras):
            self.camera_index = self.found_cameras[pos][0]

    # ─────────────────────────── IP SCAN ───────────────────────────
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
        self.found_ip_cameras = []
        self.ip_cam_combo["values"] = []
        self.scan_progress.config(text="กำลังสแกน...")
        threading.Thread(target=self._ip_scan_worker, daemon=True).start()

    def _ip_scan_worker(self):
        subnet = self.ip_range_entry.get().strip()
        user = self.cam_user.get().strip()
        password = self.cam_pass.get().strip()
        found = []
        try:
            network = ipaddress.IPv4Network(subnet, strict=False)
            hosts = list(network.hosts())
        except Exception:
            self.root.after(
                0,
                lambda: self.scan_progress.config(text="IP range ไม่ถูกต้อง"),
            )
            return
        total = len(hosts)
        for idx, host in enumerate(hosts):
            ip = str(host)
            self.root.after(
                0,
                lambda i=idx, t=total, h=ip: self.scan_progress.config(
                    text=f"สแกน {h} ({i+1}/{t})"
                ),
            )
            for port in COMMON_PORTS:
                if not self._port_open(ip, port):
                    continue
                if port in [554, 10554, 8554, 1935]:
                    for path in RTSP_PATHS:
                        url = f"rtsp://{user}:{password}@{ip}:{port}{path}"
                        if self._test_stream(url):
                            found.append((url, f"RTSP {ip}:{port}{path}"))
                            break
                if port in [8080, 80, 8081]:
                    for path in HTTP_PATHS:
                        url = f"http://{ip}:{port}{path}"
                        if self._test_stream(url):
                            found.append((url, f"HTTP {ip}:{port}{path}"))
                            break
                if found and ip in found[-1][1]:
                    break
        self.root.after(0, lambda: self._update_ip_camera_list(found))

    def _port_open(self, ip, port, timeout=0.3):
        try:
            s = socket.socket()
            s.settimeout(timeout)
            s.connect((ip, port))
            s.close()
            return True
        except Exception:
            return False

    def _test_stream(self, url, timeout=2):
        try:
            cap = cv2.VideoCapture(url)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout * 1000)
            opened = cap.isOpened()
            cap.release()
            return opened
        except Exception:
            return False

    def _update_ip_camera_list(self, found):
        self.found_ip_cameras = found
        if found:
            labels = [f[1] for f in found]
            self.ip_cam_combo["values"] = labels
            self.ip_cam_combo.current(0)
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, found[0][0])
            self.scan_progress.config(text=f"✅ พบ {len(found)} กล้อง")
        else:
            self.ip_cam_combo["values"] = ["ไม่พบกล้อง IP"]
            self.scan_progress.config(text="❌ ไม่พบกล้องใน LAN")

    def _on_ip_cam_select(self, event=None):
        pos = self.ip_cam_combo.current()
        if self.found_ip_cameras and pos < len(self.found_ip_cameras):
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, self.found_ip_cameras[pos][0])

    # ─────────────────────────── STREAM ───────────────────────────
    def start_stream(self):
        if self.is_streaming:
            return
        src_type = self.source_type.get()
        src = (
            self.camera_index
            if src_type == "Webcam"
            else self.url_entry.get().strip()
        )
        if not src and src != 0:
            messagebox.showwarning(
                "แจ้งเตือน", "กรุณาใส่ URL หรือสแกน LAN ก่อน"
            )
            return
        self.cap = cv2.VideoCapture(
            src, cv2.CAP_DSHOW if isinstance(src, int) else 0
        )
        if not self.cap.isOpened():
            messagebox.showerror("Error", f"เปิดกล้องไม่ได้:\n{src}")
            return
        self.is_streaming = True
        threading.Thread(target=self._stream_loop, daemon=True).start()
        self.status.config(text=f"กำลังสตรีม: {src}")

    def stop_stream(self):
        self.is_streaming = False
        self.auto_capture = False
        self.auto_var.set(False)
        if self.cap:
            self.cap.release()
        self.status.config(text="หยุดแล้ว")

    def _stream_loop(self):
        while self.is_streaming:
            ret, frame = self.cap.read()
            if ret:
                self.current_frame = frame
                zoomed = self._apply_zoom(frame)
                self._update_canvas(zoomed)
            time.sleep(0.01)

    def _update_canvas(self, frame):
        try:
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 380
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            fh, fw = rgb.shape[:2]
            scale = min(cw / fw, ch / fh)
            dw, dh = int(fw * scale), int(fh * scale)
            resized = cv2.resize(rgb, (dw, dh), interpolation=cv2.INTER_NEAREST)
            x1p = int(self.crop["x1"] * dw)
            y1p = int(self.crop["y1"] * dh)
            x2p = int(self.crop["x2"] * dw)
            y2p = int(self.crop["y2"] * dh)
            cv2.rectangle(resized, (x1p, y1p), (x2p, y2p), (0, 255, 0), 2)
            img = ImageTk.PhotoImage(Image.fromarray(resized))
            self.root.after(
                0,
                lambda i=img, w=dw, h=dh, coords=(
                    x1p,
                    y1p,
                    x2p,
                    y2p,
                ): self._show_canvas(i, w, h, coords),
            )
        except Exception:
            pass

    def _show_canvas(self, img, dw, dh, coords):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=img)
        self.canvas.image = img
        x1p, y1p, x2p, y2p = coords
        self.canvas.create_rectangle(
            x1p, y1p, x2p, y2p, outline="#00ff00", width=2, dash=(6, 3)
        )
        self.canvas.create_text(
            x1p + 4,
            y1p + 4,
            anchor="nw",
            text="OCR Zone",
            fill="#00ff00",
            font=("Segoe UI", 9, "bold"),
        )
        if self.zoom_level > 1.0:
            self.canvas.create_text(
                8,
                8,
                anchor="nw",
                text=f"🔍 {self.zoom_level:.1f}x  (Ctrl+ลาก = เลื่อน)",
                fill="yellow",
                font=("Segoe UI", 10, "bold"),
            )

    # ─────────────────────────── OCR ───────────────────────────
    def _crop_frame(self, frame):
        h, w = frame.shape[:2]
        x1 = max(0, int(self.crop["x1"] * w))
        y1 = max(0, int(self.crop["y1"] * h))
        x2 = min(w, int(self.crop["x2"] * w))
        y2 = min(h, int(self.crop["y2"] * h))
        return frame[y1:y2, x1:x2]

    def _preprocess(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(
            gray, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR
        )
        _, thresh = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        return thresh

    def _extract_codes(self, text):
        return list(dict.fromkeys(re.findall(r"\b\d{1,4}\b", text)))

    def _sort_ocr_result(self, text):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        numbers, texts = [], []
        for line in lines:
            digits = re.sub(r"[^0-9]", "", line)
            non_digits = re.sub(r"[0-9\s]", "", line)
            if len(digits) >= len(non_digits):
                numbers.append(line)
            else:
                texts.append(line)
        result = []
        if numbers:
            result.append("── ตัวเลข ──")
            result.extend(numbers)
        if texts:
            result.append("── ข้อความ ──")
            result.extend(texts)
        return "\n".join(result)

    def capture_and_ocr(self):
        if self.current_frame is None:
            messagebox.showinfo("แจ้งเตือน", "กรุณาเริ่มสตรีมก่อน")
            return
        threading.Thread(
            target=self._do_ocr,
            args=(self.current_frame.copy(),),
            daemon=True,
        ).start()

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Images", "*.jpg *.png *.bmp")]
        )
        if path:
            frame = cv2.imread(path)
            if frame is not None:
                self.current_frame = frame
                threading.Thread(
                    target=self._do_ocr, args=(frame.copy(),), daemon=True
                ).start()

    def _do_ocr(self, frame):
        self.root.after(0, lambda: self.status.config(text="กำลังทำ OCR..."))
        try:
            zoomed = self._apply_zoom(frame)
            cropped = self._crop_frame(zoomed)
            processed = self._preprocess(cropped)
            text_tha = pytesseract.image_to_string(
                Image.fromarray(processed),
                lang="eng",
                config="--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789",
            )
            sorted_text = self._sort_ocr_result(text_tha)
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            output = f"\n{'─'*38}\n[{ts}]\n{sorted_text}\n"
            self.root.after(0, lambda: self.result_text.insert("end", output))
            self.root.after(0, lambda: self.result_text.see("end"))
            codes = self._extract_codes(text_tha)
            if codes:
                self.root.after(
                    0, lambda c=codes: self._lookup_and_update(c, source="auto")
                )
            else:
                self.root.after(
                    0,
                    lambda: self.sheet_result.insert(
                        "end", f"\n[{ts}]\n⚠️ ไม่พบรหัส 1-4 หลักในภาพ\n"
                    ),
                )
                self.root.after(0, lambda: self.sheet_result.see("end"))
            self.root.after(
                0,
                lambda: self.status.config(
                    text=f"✅ OCR เสร็จ | รหัส: {codes} | สถานะ: {self._get_current_status()} | Auto: {'เปิด' if self.auto_capture else 'ปิด'}"
                ),
            )
        except Exception as e:
            self.root.after(
                0, lambda: self.status.config(text=f"❌ OCR error: {e}")
            )

    def save_result(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Text", "*.txt")]
        )
        if path:
            content = (
                "=== OCR Results ===\n"
                + self.result_text.get("1.0", "end")
                + "\n=== Sheet Results ===\n"
                + self.sheet_result.get("1.0", "end")
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("สำเร็จ", f"บันทึกแล้ว:\n{path}")

    def clear_result(self):
        self.result_text.delete("1.0", "end")
        self.sheet_result.delete("1.0", "end")


if __name__ == "__main__":
    root = tk.Tk()
    app = OCRScannerApp(root)
    root.protocol(
        "WM_DELETE_WINDOW", lambda: (app.stop_stream(), root.destroy())
    )
    root.mainloop()