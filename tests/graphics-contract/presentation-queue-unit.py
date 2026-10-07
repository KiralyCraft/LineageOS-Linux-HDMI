#!/usr/bin/env python3
"""Build-host policy tests using the actual Mesa queue-control functions.

No GPU/X/display calls: proves admission/target/order rules, not physical cadence.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import hashlib
import json

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('source', type=Path)
p.add_argument('--cc', default='cc')
a = p.parse_args()
s = a.source.read_text()
def body(start):
    begin=s.index('{',start); depth=0
    for end in range(begin,len(s)):
        if s[end]=='{':depth+=1
        elif s[end]=='}':
            depth-=1
            if not depth:return s[begin:end+1]
    raise ValueError('unbalanced fragment')
helper = s[s.index('static unsigned\nhdmi_pipe_outstanding'):s.index('static int\nhdmi_pipe_thread')]
helper += s[s.index('static void\nhdmi_pipe_complete_msc'):s.index('static void\nhdmi_pipe_events')]
loop = body(s.index('for (;;)',s.index('/* Preserve accepted-frame FIFO')))
wait_start=s.index('while (admission_budget &&')
wait = s[wait_start:s.index('{',wait_start)] + body(wait_start)
c = r'''
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
#define xcb_xfixes_set_region(c,id,n,r) ((void)(r))
typedef struct {int16_t x,y;uint16_t width,height;} xcb_rectangle_t;
enum phase {HDMI_FREE,HDMI_RESERVED,HDMI_PRODUCER,HDMI_COPY,HDMI_READY,HDMI_PRESENTED,HDMI_QUARANTINED};
struct hdmi_pipe_slot {enum phase phase; int interval; uint64_t order; unsigned serial; bool completed,idle,low_latency,paced_latency; unsigned valid,width,height;};
struct hdmi_pipe_generation {struct hdmi_pipe_slot slots[3];};
struct hdmi_pipe {struct hdmi_pipe_generation generations[3]; uint64_t next_order,submitted,target_msc; unsigned serial; bool primed,failed,stop,low_latency; unsigned paced_queue,paced_lead; int changed,lock;};
static unsigned presented, waits;
static uint64_t targets[9],orders[9];
static struct hdmi_pipe *waiting;
static void record(struct hdmi_pipe_slot *s,uint64_t target,unsigned options) {
 assert(presented<9);targets[presented]=target;orders[presented++]=s->order;
 assert(options==(s->interval<=0?XCB_PRESENT_OPTION_ASYNC:XCB_PRESENT_OPTION_NONE));
}
static int64_t os_time_get(void) {return 0;}
static void hdmi_pipe_time(struct hdmi_pipe *p,int stage,int64_t t) {(void)p;(void)stage;(void)t;}
static void cnd_wait(int *c,int *l) {
 (void)c;(void)l;waits++;
 for(unsigned g=0;g<3;g++)for(unsigned i=0;i<3;i++)
 if(waiting->generations[g].slots[i].phase!=HDMI_FREE &&
 !(waiting->generations[g].slots[i].phase==HDMI_PRESENTED&&waiting->generations[g].slots[i].completed)){waiting->generations[g].slots[i].phase=HDMI_FREE;return;}
 assert(0);
}
HELPER
static void submit(struct hdmi_pipe *p) { for (;;) LOOP }
static void admission(struct hdmi_pipe *p,bool ordinary,int interval) {unsigned admission_budget=hdmi_pipe_admission_budget(p,ordinary,interval);bool low_latency=ordinary&&p->low_latency&&interval==0;waiting=p;WAIT}
static void reset(struct hdmi_pipe *p) {memset(p,0,sizeof(*p));p->low_latency=true;p->paced_lead=2;p->primed=true;p->target_msc=102;p->next_order=1;presented=waits=0;}
int main(void) {
 struct hdmi_pipe p;reset(&p);
 for(unsigned n=0;n<9;n++)p.generations[n/3].slots[n%3]=(struct hdmi_pipe_slot){.phase=HDMI_READY,.order=9-n,.interval=1};
 submit(&p);assert(presented==9 && targets[0]==102 && targets[8]==110);
 for(unsigned n=0;n<9;n++)assert(orders[n]==n+1);
 puts("PASS interval-one FIFO order and original targets");
 reset(&p);
 p.generations[2].slots[0]=(struct hdmi_pipe_slot){.phase=HDMI_READY,.order=1,.low_latency=true};
 p.generations[0].slots[2]=(struct hdmi_pipe_slot){.phase=HDMI_READY,.order=2,.low_latency=true};
 assert(hdmi_pipe_outstanding(&p)==2);submit(&p);
 assert(presented==2 && targets[0]==0 && targets[1]==0 && orders[0]==1 && orders[1]==2);
 admission(&p,true,0);assert(waits==1 && hdmi_pipe_outstanding(&p)==1);
 puts("PASS interval-zero targets and cross-generation admission bound");
 reset(&p);
 p.generations[0].slots[0]=(struct hdmi_pipe_slot){.phase=HDMI_PRESENTED,.order=0,.interval=1,.completed=false};
 p.generations[0].slots[1]=(struct hdmi_pipe_slot){.phase=HDMI_READY,.order=1,.low_latency=true};
 submit(&p);assert(!presented);
 p.generations[0].slots[0].completed=true;submit(&p);assert(presented==1 && targets[0]==0);
 assert(p.generations[0].slots[0].phase==HDMI_PRESENTED&&!p.generations[0].slots[0].idle);
 puts("PASS interval change waits for completion but permits replacing active scanout");
 reset(&p);
 p.generations[0].slots[0]=(struct hdmi_pipe_slot){.phase=HDMI_PRODUCER,.order=1,.low_latency=true};
 p.generations[1].slots[1]=(struct hdmi_pipe_slot){.phase=HDMI_READY,.order=2,.low_latency=true};
 submit(&p);assert(!presented);
 p.generations[0].slots[0].phase=HDMI_READY;submit(&p);assert(presented==2 && orders[0]==1 && orders[1]==2);
 puts("PASS no ready frame overtakes an unfinished producer");
 reset(&p);
 for(unsigned i=0;i<3;i++)p.generations[0].slots[i]=(struct hdmi_pipe_slot){.phase=HDMI_PRESENTED};
 admission(&p,false,0);assert(!waits);p.failed=true;admission(&p,true,0);assert(!waits);
 p.failed=false;p.stop=true;admission(&p,true,0);assert(!waits);
 puts("PASS legacy admission and stop/fault escape");
 reset(&p);p.paced_queue=2;
 for(unsigned n=0;n<6;n++)p.generations[n/3].slots[n%3].phase=HDMI_PRESENTED;
 admission(&p,true,1);assert(waits==5 && hdmi_pipe_outstanding(&p)==1);
 reset(&p);p.paced_queue=1;
 p.generations[2].slots[1].phase=HDMI_PRESENTED;p.generations[2].slots[1].completed=true;
 admission(&p,true,1);assert(!waits&&hdmi_pipe_outstanding(&p)==1&&hdmi_pipe_pending(&p)==0);
 p.generations[0].slots[0].phase=HDMI_RESERVED;admission(&p,true,1);assert(waits==1&&hdmi_pipe_pending(&p)==0);
 assert(hdmi_pipe_admission_budget(&p,true,2)==0 && hdmi_pipe_admission_budget(&p,false,1)==0);
 p.low_latency=false;assert(hdmi_pipe_admission_budget(&p,true,1)==0);
 puts("PASS paced admission spans generations; active scanout is pinned but not counted as future work");
 reset(&p);p.paced_lead=1;struct hdmi_pipe_slot paced={.paced_latency=true};
 p.primed=false;hdmi_pipe_complete_msc(&p,&paced,100);assert(p.primed&&p.target_msc==101);
 p.target_msc=104;hdmi_pipe_complete_msc(&p,&paced,101);assert(p.target_msc==104);
 hdmi_pipe_complete_msc(&p,&paced,105);assert(p.target_msc==106);
 paced.paced_latency=false;p.primed=false;hdmi_pipe_complete_msc(&p,&paced,100);assert(p.target_msc==102);
 puts("PASS one-refresh priming, preservation of queued future targets, late re-priming and legacy lead");
}
'''.replace('HELPER',helper).replace('LOOP',loop).replace('WAIT}',wait+'}')
with tempfile.TemporaryDirectory(prefix='hdmi-queue-unit-') as tmp:
    f=Path(tmp)/'test.c'; f.write_text(c)
    exe=Path(tmp)/'test'
    subprocess.run([a.cc,'-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(f),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True,timeout=10)
print(json.dumps({'pipeline_sha256':hashlib.sha256(s.encode()).hexdigest(),'scope':'stubbed policy tests, no GPU or physical display'}))
