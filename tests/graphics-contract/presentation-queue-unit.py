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
loop = body(s.index('for (;;)',s.index('/* Preserve accepted-frame FIFO')))
wait_start=s.index('while (low_latency &&')
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
enum phase {HDMI_FREE,HDMI_RESERVED,HDMI_PRODUCER,HDMI_COPY,HDMI_READY,HDMI_PRESENTED,HDMI_QUARANTINED};
struct hdmi_pipe_slot {enum phase phase; int interval; uint64_t order; unsigned serial; bool completed,idle,low_latency;};
struct hdmi_pipe_generation {struct hdmi_pipe_slot slots[3];};
struct hdmi_pipe {struct hdmi_pipe_generation generations[3]; uint64_t next_order,submitted,target_msc; unsigned serial; bool primed,failed,stop; int changed,lock;};
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
 if(waiting->generations[g].slots[i].phase!=HDMI_FREE){waiting->generations[g].slots[i].phase=HDMI_FREE;return;}
 assert(0);
}
HELPER
static void submit(struct hdmi_pipe *p) { for (;;) LOOP }
static void admission(struct hdmi_pipe *p,bool low_latency) {waiting=p;WAIT}
static void reset(struct hdmi_pipe *p) {memset(p,0,sizeof(*p));p->primed=true;p->target_msc=102;p->next_order=1;presented=waits=0;}
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
 admission(&p,true);assert(waits==1 && hdmi_pipe_outstanding(&p)==1);
 puts("PASS interval-zero targets and cross-generation admission bound");
 reset(&p);
 p.generations[0].slots[0]=(struct hdmi_pipe_slot){.phase=HDMI_PRESENTED,.order=0,.interval=1,.completed=true};
 p.generations[0].slots[1]=(struct hdmi_pipe_slot){.phase=HDMI_READY,.order=1,.low_latency=true};
 submit(&p);assert(!presented);
 p.generations[0].slots[0].phase=HDMI_FREE;submit(&p);assert(presented==1 && targets[0]==0);
 puts("PASS interval change cannot overtake scheduled consumer-owned slot");
 reset(&p);
 p.generations[0].slots[0]=(struct hdmi_pipe_slot){.phase=HDMI_PRODUCER,.order=1,.low_latency=true};
 p.generations[1].slots[1]=(struct hdmi_pipe_slot){.phase=HDMI_READY,.order=2,.low_latency=true};
 submit(&p);assert(!presented);
 p.generations[0].slots[0].phase=HDMI_READY;submit(&p);assert(presented==2 && orders[0]==1 && orders[1]==2);
 puts("PASS no ready frame overtakes an unfinished producer");
 reset(&p);
 for(unsigned i=0;i<3;i++)p.generations[0].slots[i]=(struct hdmi_pipe_slot){.phase=HDMI_PRESENTED};
 admission(&p,false);assert(!waits);p.failed=true;admission(&p,true);assert(!waits);
 p.failed=false;p.stop=true;admission(&p,true);assert(!waits);
 puts("PASS legacy admission and stop/fault escape");
}
'''.replace('HELPER',helper).replace('LOOP',loop).replace('WAIT}',wait+'}')
with tempfile.TemporaryDirectory(prefix='hdmi-queue-unit-') as tmp:
    f=Path(tmp)/'test.c'; f.write_text(c)
    exe=Path(tmp)/'test'
    subprocess.run([a.cc,'-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(f),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True,timeout=10)
print(json.dumps({'pipeline_sha256':hashlib.sha256(s.encode()).hexdigest(),'scope':'stubbed policy tests, no GPU or physical display'}))
