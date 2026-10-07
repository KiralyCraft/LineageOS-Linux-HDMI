#!/usr/bin/env python3
"""Build-host-only control-flow probes; no GPU, X server or display measurement.

Compile the supplied production glxgears event loop and HDMI pipeline admission/
submission fragments with deterministic fixtures. These establish queue policy,
not that a particular physical symptom occurred or its duration. All native
compilation must run on the designated build server.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('mesa', type=Path)
p.add_argument('gears', type=Path)
p.add_argument('--cc', default='cc')
a = p.parse_args()
pipe = (a.mesa / 'src/gallium/frontends/dri/loader_dri3_hdmi_pipeline.h').read_text()
gears = a.gears.read_text()

def braced(text, start):
    opening = text.index('{', start)
    level = 0
    for end in range(opening, len(text)):
        if text[end] == '{': level += 1
        elif text[end] == '}':
            level -= 1
            if not level: return text[opening:end+1]
    raise ValueError('unbalanced production fragment')

submit = braced(pipe, pipe.index('for (;;)', pipe.index('/* Preserve accepted-frame FIFO')))
wait = braced(pipe, pipe.index('while (g &&', pipe.index('static bool\nhdmi_pipe_present(')))
events = braced(gears, gears.index('event_loop(Display *dpy, Window win)'))
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define HDMI_PIPE_GENERATIONS 3
#define HDMI_PIPE_SLOTS 3
#define MAX2(a,b) ((a)>(b)?(a):(b))
#define p_atomic_read(p) (*(p))
#define XCB_PRESENT_OPTION_ASYNC 1
#define XCB_PRESENT_OPTION_NONE 0
#define HDMI_SLOT_WAIT 0
#define xcb_present_pixmap(...) record(next,target,options)
#define xcb_flush(...) ((void)0)
enum phase {HDMI_FREE,HDMI_RESERVED,HDMI_PRODUCER,HDMI_COPY,HDMI_READY,HDMI_PRESENTED,HDMI_QUARANTINED};
struct hdmi_pipe_slot {enum phase phase; int interval; uint64_t order; unsigned serial; bool completed,idle;};
struct hdmi_pipe_generation {struct hdmi_pipe_slot slots[3];};
struct hdmi_pipe {struct hdmi_pipe_generation generations[3]; uint64_t next_order,submitted,target_msc; unsigned serial; bool primed,failed,stop; int changed,lock;};
static unsigned presented, waits, async_count;
static uint64_t targets[9],orders[9];
static struct hdmi_pipe_generation *waiting;
static void record(struct hdmi_pipe_slot *s,uint64_t target,unsigned options) {
 assert(presented<9);targets[presented]=target;orders[presented++]=s->order;
 if(options&XCB_PRESENT_OPTION_ASYNC) async_count++;
}
static __attribute__((unused)) int64_t os_time_get(void) {return 0;}
static __attribute__((unused)) void hdmi_pipe_time(struct hdmi_pipe *p,int stage,int64_t t) {(void)p;(void)stage;(void)t;}
static void cnd_wait(int *c,int *l) {(void)c;(void)l;waits++;waiting->slots[0].phase=HDMI_FREE;}
static void submit(struct hdmi_pipe *p) { for (;;) SUBMIT }
static void admission(struct hdmi_pipe *p) {
 struct hdmi_pipe_generation *g=&p->generations[0];
 struct hdmi_pipe_slot *s=NULL;waiting=g;
 while(g && !p_atomic_read(&p->failed) && !p_atomic_read(&p->stop)) WAIT
 assert(s);
}
static void reset(struct hdmi_pipe *p,int interval) {
 memset(p,0,sizeof(*p));p->primed=true;p->target_msc=102;p->next_order=1;
 presented=waits=async_count=0;
 for(unsigned n=0;n<9;n++) {
  struct hdmi_pipe_slot *s=&p->generations[n/3].slots[n%3];
  s->phase=HDMI_READY;s->interval=interval;s->order=9-n;
 }
}
/* Event queue fixture: only Configure, Expose, Exit; GL/X calls are stubbed. */
typedef void Display;
typedef unsigned Window;
typedef struct {int kind,width;} XEvent;
enum {CONFIGURE,EXPOSE,QUIT,NOP,DRAW,EXIT};
static XEvent queue[300];static unsigned qsize,qpos,draws,viewport,last_viewport;
static int animate=1;
static int XPending(Display *d) {(void)d;return qpos<qsize;}
static void XNextEvent(Display *d,XEvent *e) {(void)d;assert(qpos<qsize);*e=queue[qpos++];}
static int handle_event(Display *d,Window w,XEvent *e) {
 (void)d;(void)w;
 if(e->kind==CONFIGURE) {viewport=e->width;return NOP;}
 if(e->kind==EXPOSE) return DRAW;
 return EXIT;
}
static void draw_frame(Display *d,Window w) {(void)d;(void)w;draws++;assert(viewport>=last_viewport);last_viewport=viewport;}
static void event_loop(Display *dpy,Window win) EVENTS
static unsigned replay(bool expose_each) {
 qsize=qpos=draws=viewport=last_viewport=0;
 for(int n=1;n<=120;n++) {
  queue[qsize++]=(XEvent){CONFIGURE,n};
  if(expose_each) queue[qsize++]=(XEvent){EXPOSE,0};
 }
 if(!expose_each) queue[qsize++]=(XEvent){EXPOSE,0};
 queue[qsize++]=(XEvent){QUIT,0};event_loop(NULL,0);assert(last_viewport==120);return draws;
}
int main(void) {
 struct hdmi_pipe p;reset(&p,0);submit(&p);
 assert(presented==9 && async_count==9 && targets[0]==102 && targets[8]==110);
 for(unsigned n=0;n<9;n++)assert(orders[n]==n+1);
 puts("{\"case\":\"interval_zero_submission\",\"last_completed_msc_fixture\":100,\"first_target\":102,\"last_target\":110,\"submitted\":9,\"fifo_order\":true,\"async_options\":true}");
 reset(&p,1);submit(&p);assert(presented==9 && !async_count && targets[8]==110);
 reset(&p,0);p.generations[2].slots[2].phase=HDMI_PRODUCER;submit(&p);assert(!presented);
 puts("{\"case\":\"oldest_not_ready\",\"later_ready\":8,\"submitted\":0}");
 reset(&p,0);for(unsigned n=0;n<3;n++)p.generations[0].slots[n].phase=HDMI_PRESENTED;
 admission(&p);assert(waits==1);
 puts("{\"case\":\"interval_zero_full_slots\",\"condition_wait_called\":true}");
 unsigned each=replay(true),last=replay(false);assert(each==120 && last==1);
 printf("{\"case\":\"glxgears_event_replay\",\"configure_events\":120,\"draws_expose_each\":%u,\"draws_single_final_expose\":%u}\n",each,last);
}
'''.replace('SUBMIT', submit).replace('WAIT\n', wait+'\n').replace('EVENTS\n', events+'\n')
with tempfile.TemporaryDirectory(prefix='hdmi-offline-policy-') as directory:
    root=Path(directory);(root/'probe.c').write_text(fixture)
    subprocess.run([a.cc,'-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(root/'probe.c'),'-o',str(root/'probe')],check=True)
    subprocess.run([str(root/'probe')],check=True,timeout=10)
print(json.dumps({'sources_sha256': {'pipeline':hashlib.sha256(pipe.encode()).hexdigest(),'glxgears':hashlib.sha256(gears.encode()).hexdigest()},'scope':'production control-flow fragments with stubs; no physical display or GPU timing'}))
