from PIL import Image
Image.open("RTM.png").save("RTM.ico", sizes=[(16,16),(32,32),(48,48),(256,256)])
