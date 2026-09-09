import cv2

class CameraManager:
    def list_webcams(self, max_check=5):
        available = []
        for i in range(max_check):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                available.append(f"Camera {i}")
                cap.release()
        return available if available else ["No camera found"]

    def test_connection(self, source):
        try:
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                return False, "Cannot open"
            ret, _ = cap.read()
            cap.release()
            return (True, "OK") if ret else (False, "No frame")
        except Exception as e:
            return False, str(e)