ภาพรวมของโปรเจกต์ (Overview)
ระบบช่วยจัดการงานหน้าร้านสำหรับธุรกิจรับปักชื่อ/ปักเสื้อผ้า พัฒนาขึ้นเพื่อแก้ปัญหาการบันทึกข้อมูลล่าช้าและป้องกันการตกหล่นของออเดอร์ที่ลูกค้านำมาส่ง โดยใช้กล้องสแกนใบสั่งงานหรือรายละเอียดของลูกค้า แล้วนำภาพมาประมวลผลแปลงเป็นข้อความ (OCR) โดยอัตโนมัติ จากนั้นระบบจะบันทึกข้อมูลขึ้น Google Sheets ทันที พร้อมระบบติดตามสถานะงานแบบเรียลไทม์ (เช่น ยังไม่ได้ปัก, กำลังปัก, ปักเสร็จแล้ว) เพื่อให้เจ้าหน้าที่และช่างปักตรวจเช็กขั้นตอนการทำงานได้อย่างแม่นยำ

ฟีเจอร์หลัก (Key Features)

Real-time Camera Stream: ดึงภาพสดจากกล้อง IP Camera เพื่อจับภาพใบสั่งงานหรือรายละเอียดลูกค้าหน้าร้าน

Automated OCR: ตรวจจับและสกัดข้อความภาษาไทย/อังกฤษจากใบงานเข้าสู่ระบบอัตโนมัติ ลดความผิดพลาดจากการพิมพ์ด้วยมือ

Cloud Database Integration: บันทึกข้อมูลลูกค้า วันที่ และรายละเอียดงานลง Google Sheets แบบอัตโนมัติ

Status Tracking System: ติดตามสถานะของแต่ละชิ้นงานได้อย่างชัดเจน (ยังไม่ได้ปัก / กำลังดำเนินการ / ปักเสร็จสิ้น) ป้องกันการส่งมอบงานผิดพลาดหรือออเดอร์ตกหล่น

Standalone Executable: รองรับการใช้งานผ่านไฟล์ .exe บนระบบปฏิบัติการ Windows โดยไม่ต้องลงโปรแกรมหรือไลบรารีอื่นเพิ่มเติม

เครื่องมือและเทคโนโลยีที่ใช้ (Tech Stack)

Language: Python 3

User Interface (GUI): Tkinter

Computer Vision & Image Processing: OpenCV (cv2)

Text Recognition (OCR): Tesseract-OCR (pytesseract)

Cloud & Database: Google Sheets API, Google Drive API (gspread, oauth2client)

Camera Streaming Protocol: RTSP / ONVIF Protocol

Packaging & Deployment: PyInstaller (แปลงไฟล์เป็น Windows Executable)

Version Control: Git, GitHub
