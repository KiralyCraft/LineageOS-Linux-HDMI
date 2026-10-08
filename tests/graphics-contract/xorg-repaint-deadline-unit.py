#!/usr/bin/env python3
"""Compile real repaint timing/state functions; run on the native build host."""
from pathlib import Path
import subprocess,sys,tempfile
src=Path(sys.argv[1]).read_text()
part=src[src.index('static unsigned\nms_tearfree_repaint_delay('):src.index('static void ms_tearfree_copy_clear(')]
fixture=r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
typedef int Bool;
typedef uint32_t CARD32;
#define TRUE 1
#define FALSE 0
struct timer { int active; };
typedef struct timer *OsTimerPtr;
struct mode { double refresh; };
struct drmmode { unsigned tearfree_repaint_us; Bool tearfree_async; };
struct tearfree { OsTimerPtr repaint_timer; Bool repaint_ready,repaint_pending; uint64_t last_flip_ust; };
struct dc { struct tearfree tearfree; struct drmmode *drmmode; };
typedef struct dc *drmmode_crtc_private_ptr;
typedef struct tearfree *drmmode_tearfree_ptr;
struct crtc { void *driver_private; struct mode mode; };
typedef struct crtc *xf86CrtcPtr;
static uint64_t now_us;
static unsigned timer_calls,timer_delay;
static struct timer storage;
static double xf86ModeVRefresh(struct mode *m) { return m->refresh; }
static uint64_t GetTimeInMicros(void) { return now_us; }
static OsTimerPtr TimerSet(OsTimerPtr t,unsigned flags,unsigned delay,CARD32(*ready)(OsTimerPtr,CARD32,void*),void*data) {
 (void)t;(void)flags;(void)ready;(void)data;timer_calls++;timer_delay=delay;storage.active=1;return &storage;
}
#define FatalError(s) abort()
/* PRODUCTION */
int main(void) {
 assert(ms_tearfree_repaint_delay(1000000,1000100,33333,8000)==25);
 assert(ms_tearfree_repaint_delay(1000000,1010000,33333,8000)==15);
 assert(ms_tearfree_repaint_delay(1000000,1026000,33333,8000)==0);
 assert(ms_tearfree_repaint_delay(1000000,1033500,33333,8000)==25);
 assert(ms_tearfree_repaint_delay(0,1000100,33333,8000)==0);
 assert(ms_tearfree_repaint_delay(1000000,999999,33333,8000)==0);
 assert(ms_tearfree_repaint_delay(1000000,2000001,33333,8000)==0);
 assert(ms_tearfree_repaint_delay(1000000,1000100,33333,0)==0);
 assert(ms_tearfree_repaint_delay(1000000,1000100,8000,8000)==0);
 assert(ms_tearfree_repaint_delay(1000000,1000100,0,8000)==0);
 assert(ms_tearfree_repaint_delay(1000000,1000100,16666,8000)==8);
 struct drmmode mode={8000,TRUE};struct dc dc={.drmmode=&mode};struct crtc c={.driver_private=&dc,.mode={30.0}};
 dc.tearfree.last_flip_ust=1000000;now_us=1000100;
 assert(ms_tearfree_wait_repaint(&c));assert(timer_calls==1&&timer_delay==25&&dc.tearfree.repaint_pending);
 now_us=1010000;assert(ms_tearfree_wait_repaint(&c));assert(timer_calls==1);
 assert(ms_tearfree_repaint_ready(dc.tearfree.repaint_timer,0,&c)==0);
 assert(!dc.tearfree.repaint_pending&&dc.tearfree.repaint_ready);
 assert(!ms_tearfree_wait_repaint(&c));assert(!dc.tearfree.repaint_ready&&timer_calls==1);
 mode.tearfree_async=FALSE;assert(!ms_tearfree_wait_repaint(&c));assert(timer_calls==1);
 for(unsigned period=5000;period<=100000;period+=1000)
  for(unsigned age=0;age<1000000;age+=997) {
   unsigned delay=ms_tearfree_repaint_delay(1000000,1000000+age,period,8000);
   if(delay)assert((uint64_t)delay*1000+8000<=period-age%period);
  }
 puts("PASS: production repaint deadline, disabled/stale/future timelines, quantization, single timer and readiness consumption");
}
'''
with tempfile.TemporaryDirectory(prefix='hdmi-repaint-unit-') as temp:
 root=Path(temp);(root/'test.c').write_text(fixture.replace('/* PRODUCTION */',part))
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Wno-unused-parameter','-g','-O1','-fsanitize=address,undefined',str(root/'test.c'),'-o',str(root/'test')],check=True)
 subprocess.run([str(root/'test')],check=True,timeout=20)
