import cv2
import re
import time
import gspread
from ultralytics import YOLO
import easyocr

# 1. โหลดโมเดล YOLO และ EasyOCR
print("กำลังโหลดโมเดล...")
yolo_model = YOLO("best.pt")
reader = easyocr.Reader(['en'], gpu=False)

# 2. เชื่อมต่อ Google Sheets เตรียมไว้ล่วงหน้า
print("กำลังเชื่อมต่อ Google Sheets...")
try:
    gc = gspread.service_account(filename="credentials.json")
    sh = gc.open("VSearch4.0")
    worksheet = sh.worksheet("2026")
    print("เชื่อมต่อ Google Sheets สำเร็จ!")
except Exception as e:
    print(f"[Sheet Connect Error] {e}")
    worksheet = None

def update_sheet(code_text):
    if worksheet is None:
        print("[Sheet] ข้ามการบันทึก: ไม่ได้เชื่อมต่อ Google Sheet")
        return
    try:
        # ค้นหารหัสในตาราง
        cell = worksheet.find(code_text)
        if cell:
            # อัปเดตคอลัมน์ถัดไป (col + 1) เป็นสถานะ "ปักเสร็จแล้ว"
            worksheet.update_cell(cell.row, cell.col + 1, "ปักเสร็จแล้ว")
            print(f"✅ [Sheet] อัปเดตรหัส {code_text} สำเร็จ!")
        else:
            print(f"⚠️ [Sheet] ไม่พบรหัส {code_text} ในชีต")
    except Exception as e:
        print(f"❌ [Sheet Error] {e}")

# 3. เชื่อมต่อกล้อง VStarcam
rtsp_url = "rtsp://admin:Sitthik0rn@192.168.1.124:10554/tcp/av0_0"
cap = cv2.VideoCapture(rtsp_url)

if not cap.isOpened():
    print("❌ ไม่สามารถเชื่อมต่อกล้อง RTSP ได้ กรุณาตรวจสอบ IP หรือเครือข่าย Wi-Fi")

# ตัวแปรควบคุมเพื่อไม่ให้อัปเดตรหัสเดิมซ้ำรัวๆ
last_code = ""
last_time = 0

print("🟢 เริ่มระบบตรวจจับ... กด 'q' ที่หน้าต่างวิดีโอเพื่อปิดโปรแกรม")

while cap.isOpened():
    ret, frame = cap.read()
    if not ret or frame is None or frame.size == 0:
        continue

    # ตรวจจับตำแหน่งตัวเลขด้วย YOLOv8n
    results = yolo_model(frame, conf=0.4, verbose=False)
    
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            
            # เผื่อขอบเล็กน้อย (Padding)
            h, w = frame.shape[:2]
            pad = 8
            crop_x1 = max(0, x1 - pad)
            crop_y1 = max(0, y1 - pad)
            crop_x2 = min(w, x2 + pad)
            crop_y2 = min(h, y2 + pad)
            
            cropped = frame[crop_y1:crop_y2, crop_x1:crop_x2]
            if cropped.size == 0:
                continue

            # ส่งเฉพาะกรอบที่ตัดให้ EasyOCR อ่านตัวเลข 0-9
            ocr_res = reader.readtext(cropped, allowlist='0123456789')
            for _, text, prob in ocr_res:
                clean_text = text.strip()
                # ตรวจสอบว่าเป็นตัวเลข 4 หลักตรงเป๊ะ และความมั่นใจเกิน 40%
                if re.fullmatch(r'\d{4}', clean_text) and prob > 0.4:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, clean_text, (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                    
                    # ป้องกันการบันทึกซ้ำ (ถ้าเป็นรหัสเดิม ต้องเว้นอย่างน้อย 5 วินาที)
                    current_time = time.time()
                    if clean_text != last_code or (current_time - last_time > 5):
                        print(f"🎯 อ่านได้รหัส: {clean_text} (ความมั่นใจ {prob:.2f})")
                        update_sheet(clean_text)
                        last_code = clean_text
                        last_time = current_time

    # แสดงผลวิดีโอ
    cv2.imshow("Garment Tracking Pipeline", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()