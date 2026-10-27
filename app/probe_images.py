"""Tiny standard-library PNG generator for a real image-only marker probe."""
import base64,secrets,struct,zlib
GLYPHS={
 '0':['111','101','101','101','111'],'1':['010','110','010','010','111'],
 '2':['111','001','111','100','111'],'3':['111','001','111','001','111'],
 '4':['101','101','111','001','001'],'5':['111','100','111','001','111'],
 '6':['111','100','111','101','111'],'7':['111','001','010','010','010'],
 '8':['111','101','111','101','111'],'9':['111','101','111','001','111']}

def make_image_probe():
 marker=''.join(str(secrets.randbelow(10)) for _ in range(6));scale=12
 width=(len(marker)*5+2)*scale;height=9*scale
 pixels=[bytearray([255]*width) for _ in range(height)]
 for i,char in enumerate(marker):
  for y,row in enumerate(GLYPHS[char]):
   for x,value in enumerate(row):
    if value=='1':
     for yy in range((y+2)*scale,(y+3)*scale):
      pixels[yy][(i*5+x+2)*scale:(i*5+x+3)*scale]=b'\0'*scale
 def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
 png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,0,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(b'\0'+row for row in pixels)))+chunk(b'IEND',b'')
 return 'data:image/png;base64,'+base64.b64encode(png).decode(),marker
