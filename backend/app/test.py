# import cv2 
# rtsp_url = "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.176/axis-media/media.amp" 
# cap = cv2.VideoCapture(rtsp_url) 
# if not cap.isOpened():     
#     print("Could not open RTSP stream")     
#     raise SystemExit 
# while True:     
#     ret, frame = cap.read()     
#     if not ret:         
#         print("Could not read frame")         
#         break     
#     cv2.imshow("RTSP Stream", frame)     
#     if cv2.waitKey(1) & 0xFF == ord("q"):         
#         break 
# cap.release() 
# cv2.destroyAllWindows()

import requests
 
url = "http://10.182.55.14:8000/live-counts"
 
payload = {
    "streams": [
        {
            "stream_id": "cam_01",
            "source": "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.176/axis-media/media.amp",
            "conf": 0.5,
            "iou": 0.45,
            "regions": {
                "region_left": [[0, 1053], [769, 349], [561, 243], [0, 464]],
                "region_right": [[1920, 972], [1100, 369], [1323, 160], [1920, 356]]
            }
        },
        {
            "stream_id": "cam_02",
            "source": "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.168/axis-media/media.amp",
            "conf": 0.5,
            "iou": 0.45,
            "regions": {
                "region_left": [[59, 1078], [708, 234], [436, 160], [34, 389]],
                "region_right": [[1967, 1071], [1126, 227], [1401, 178], [1974, 349]],
            }
        },
        {
            "stream_id": "cam_03",
            "source": "rtsp://ukcamviz:qUoTuRNytxe6EH@10.29.18.169/axis-media/media.amp",
            "conf": 0.5,
            "iou": 0.45,
            "regions": {
                "region_left": [[84, 1078], [800, 155], [514, 72], [34, 356], [93, 1067]],
                "region_right": [[1948, 1076], [1130, 170], [1324, 60], [1977, 360], [1977, 1069]],
            }
        }
    ]
}
 
response = requests.post(url, json=payload, timeout=120)
 
print("Status:", response.status_code)
print(response.json())