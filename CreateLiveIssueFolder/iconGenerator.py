from PIL import Image
Image.open("LiveIssue.png").save("LiveIssue.ico", sizes=[(16,16),(32,32),(48,48),(256,256)])
