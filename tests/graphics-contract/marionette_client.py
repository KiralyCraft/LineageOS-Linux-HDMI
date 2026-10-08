"""Bounded Marionette client for an explicitly isolated browser test.
"""
import socket,json
class Marionette:
 def __init__(self,port=2829):
  self.s=socket.create_connection(('127.0.0.1',port),timeout=20);self.s.settimeout(25);self.ids=0;self.greeting=self.read()
 def read(self):
  size=bytearray()
  while True:
   b=self.s.recv(1)
   if not b:raise EOFError('Marionette closed')
   if b==b':':break
   size.extend(b)
   assert len(size)<=12
  n=int(size);assert n<=32*1024*1024;data=bytearray()
  while len(data)<n:
   b=self.s.recv(n-len(data))
   if not b:raise EOFError('Marionette body closed')
   data.extend(b)
  return json.loads(data)
 def call(self,command,parameters=None):
  self.ids+=1;data=json.dumps([0,self.ids,command,parameters or {}]).encode();self.s.sendall(str(len(data)).encode()+b':'+data)
  msg=self.read();assert msg[0]==1 and msg[1]==self.ids,msg
  if msg[2]:raise RuntimeError(msg[2])
  return msg[3]
 def close(self):self.s.close()
