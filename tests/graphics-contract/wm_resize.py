"""Bounded interactive resizing of an owned test window through the active WM.

Uses the standard _NET_WM_MOVERESIZE message and XTest pointer motion. The caller
must own the target window. Always release the synthetic button, cancel the WM
operation on error, and restore the saved pointer position and input focus.
"""
import ctypes as C
import time

V,I,U,L=C.c_void_p,C.c_int,C.c_uint,C.c_ulong
class Data(C.Union):
    _fields_=[('l',C.c_long*5),('b',C.c_char*20)]
class Message(C.Structure):
    _fields_=[('type',I),('serial',L),('send_event',I),('display',V),('window',L),
              ('message_type',L),('format',I),('data',Data)]
class Event(C.Union):
    _fields_=[('message',Message),('pad',C.c_long*24)]

class WMResize:
    def __init__(self,display,xlib,root,screen,window,bounds):
        self.d,self.X,self.root,self.screen,self.window=display,xlib,root,screen,window
        self.bounds=bounds;self.started=False;self.down=False;self.closed=False
        def bind(lib,name,ret,types):
            f=getattr(lib,name);f.restype,f.argtypes=ret,types;return f
        self.sync=bind(xlib,'XSync',I,[V,I]);self.flush=bind(xlib,'XFlush',I,[V])
        self.atom=bind(xlib,'XInternAtom',L,[V,C.c_char_p,I])
        self.send=bind(xlib,'XSendEvent',I,[V,L,I,C.c_long,C.POINTER(Event)])
        self.set_focus=bind(xlib,'XSetInputFocus',I,[V,L,I,L])
        get_focus=bind(xlib,'XGetInputFocus',I,[V,C.POINTER(L),C.POINTER(I)])
        self.old_focus,self.revert=L(),I();get_focus(display,C.byref(self.old_focus),C.byref(self.revert))
        rr,child,rx,ry,wx,wy,mask=L(),L(),I(),I(),I(),I(),U()
        query=bind(xlib,'XQueryPointer',I,[V,L,C.POINTER(L),C.POINTER(L),C.POINTER(I),C.POINTER(I),C.POINTER(I),C.POINTER(I),C.POINTER(U)])
        assert query(display,root,C.byref(rr),C.byref(child),C.byref(rx),C.byref(ry),C.byref(wx),C.byref(wy),C.byref(mask))
        assert not mask.value&0x1f00,'A real button is held; synthetic resizing not started'
        self.old_pointer=(rx.value,ry.value)
        self.T=C.CDLL('libXtst.so.6')
        self.motion=bind(self.T,'XTestFakeMotionEvent',I,[V,I,I,I,L])
        self.button=bind(self.T,'XTestFakeButtonEvent',I,[V,U,I,L])
        self.records=[]
    def message(self,direction,x,y,button):
        event=Event();event.message=Message(type=33,display=self.d,window=self.window,
            message_type=self.atom(self.d,b'_NET_WM_MOVERESIZE',0),format=32)
        event.message.data.l[:]=(x,y,direction,button,2)
        assert self.send(self.d,self.root,0,(1<<20)|(1<<19),C.byref(event))
        self.sync(self.d,0)
    def start(self):
        x,y,w,h=self.bounds(self.window);self.origin=(x,y);self.initial=(w,h)
        px,py=x+w-2,y+h-2
        assert self.motion(self.d,self.screen,px,py,0);self.sync(self.d,0)
        assert self.button(self.d,1,1,0);self.down=True;self.sync(self.d,0)
        self.message(4,px,py,1);self.started=True
        time.sleep(.1)
    def move(self,width,height):
        assert self.started and not self.closed
        x,y=self.origin;px,py=x+width-2,y+height-2
        before=time.monotonic_ns();assert self.motion(self.d,self.screen,px,py,0)
        self.sync(self.d,0)
        row=dict(ns=before,requested=[width,height],actual=self.bounds(self.window),request_ms=(time.monotonic_ns()-before)/1e6)
        self.records.append(row);return row
    def finish(self):
        if self.down:
            self.button(self.d,1,0,0);self.down=False;self.sync(self.d,0)
        self.started=False
    def close(self):
        if self.closed:return
        if self.started:self.message(11,0,0,0)
        self.finish()
        self.motion(self.d,self.screen,*self.old_pointer,0)
        # The original desktop window is still present in this isolated test.
        self.set_focus(self.d,self.old_focus.value,self.revert.value,0)
        self.sync(self.d,0);self.closed=True
