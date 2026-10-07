#!/usr/bin/env python3
"""Compile the real Present consumer-release helpers with lifetime/fence stubs.

Run on the build host. This checks ownership and notification ordering, not GPU
visibility. Error paths run in children and must fail without notifying idle.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('source',type=Path)
p.add_argument('--cc',default='cc')
a=p.parse_args();s=a.source.read_text()
code=s[s.index('struct present_copy_release {'):s.index('void\npresent_execute_copy(')]
c=r'''
#include <assert.h>
#include <errno.h>
#include <poll.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#include <linux/sync_file.h>
#define TRUE 1
#define FALSE 0
#define X_NOTIFY_READ 1
#define X_NOTIFY_ERROR 2
typedef int Bool;typedef uint32_t CARD32;
struct xorg_list {struct xorg_list *next,*prev;};
static void xorg_list_init(struct xorg_list *h){h->next=h->prev=h;}
static bool xorg_list_is_empty(struct xorg_list *h){return h->next==h;}
static void xorg_list_append(struct xorg_list *n,struct xorg_list *h){n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n;}
static void xorg_list_del(struct xorg_list *n){n->prev->next=n->next;n->next->prev=n->prev;}
#define entry(n,t,m) ((t*)((char*)(n)-offsetof(t,m)))
#define xorg_list_for_each_entry(p,h,m) for(struct xorg_list *it=(h)->next;it!=(h)&&((p)=entry(it,__typeof__(*(p)),m),1);it=it->next)
#define xorg_list_for_each_entry_safe(p,t,h,m) for(struct xorg_list *it=(h)->next,*nxt=it->next;it!=(h)&&((p)=entry(it,__typeof__(*(p)),m),(t)=nxt==(h)?NULL:entry(nxt,__typeof__(*(t)),m),1);it=nxt,nxt=it->next)
struct present_screen_priv;
typedef struct screen {struct present_screen_priv *priv;} *ScreenPtr;
typedef struct window {struct {ScreenPtr pScreen;} drawable;} *WindowPtr;
typedef struct pixmap {int refcnt;struct {unsigned id;} drawable;} *PixmapPtr;
struct present_fence {int alive;};
typedef struct present_screen_priv {struct xorg_list copy_releases;int(*copy_export_fence)(ScreenPtr);void(*copy_finish)(ScreenPtr);} *present_screen_priv_ptr;
#define present_screen_priv(s) ((s)->priv)
typedef struct vblank {WindowPtr window;PixmapPtr pixmap;CARD32 serial;struct present_fence *idle_fence;} *present_vblank_ptr;
typedef struct timer {int unused;} *OsTimerPtr;
static int idle_events,fence_triggers,wrappers_freed,pixmaps_freed,registered,timers,finishes,status,pipefds[2],expected_fatal;
static void present_pixmap_idle(PixmapPtr p,WindowPtr w,CARD32 serial,struct present_fence *f) {assert(p->refcnt>0);assert(serial==7);if(w)idle_events++;if(f&&f->alive)fence_triggers++;}
static void present_fence_destroy(struct present_fence *f){if(f){wrappers_freed++;free(f);}}
static void dixDestroyPixmap(PixmapPtr p,unsigned id){assert(p->drawable.id==id);assert(p->refcnt>0);if(!--p->refcnt){pixmaps_freed++;free(p);}}
static Bool SetNotifyFd(int fd,void(*cb)(int,int,void*),int mask,void*data){(void)fd;(void)cb;(void)mask;(void)data;registered++;return TRUE;}
static void RemoveNotifyFd(int fd){(void)fd;assert(registered>0);registered--;}
static OsTimerPtr TimerSet(OsTimerPtr old,int flags,CARD32 delay,CARD32(*cb)(OsTimerPtr,CARD32,void*),void*data){(void)old;(void)flags;(void)delay;(void)cb;(void)data;timers++;return calloc(1,sizeof(struct timer));}
static void TimerFree(OsTimerPtr t){assert(timers>0);timers--;free(t);}
static int fake_ioctl(int fd,unsigned long request,struct sync_file_info *info){(void)fd;(void)request;info->status=status;return 0;}
#define ioctl fake_ioctl
static __attribute__((noreturn)) void FatalError(const char *fmt,...){(void)fmt;_exit(expected_fatal&&!idle_events&&!fence_triggers?86:87);}
CODE
static int export_fence(ScreenPtr screen){(void)screen;return dup(pipefds[0]);}
static void finish(ScreenPtr screen){(void)screen;finishes++;status=1;}
static struct present_screen_priv priv;
static struct screen screen={&priv};
static struct window window={{&screen}};
static void setup(void){
 idle_events=fence_triggers=wrappers_freed=pixmaps_freed=registered=timers=finishes=status=expected_fatal=0;
 memset(&priv,0,sizeof(priv));xorg_list_init(&priv.copy_releases);assert(pipe(pipefds)==0);
 assert(present_set_copy_release(&screen,export_fence,finish));
}
static struct vblank frame(void){PixmapPtr p=calloc(1,sizeof(*p));p->refcnt=1;p->drawable.id=11;struct present_fence*f=calloc(1,sizeof(*f));f->alive=1;return(struct vblank){&window,p,7,f};}
static void destroy_vblank(struct vblank *v){present_fence_destroy(v->idle_fence);dixDestroyPixmap(v->pixmap,11);}
static struct present_copy_release *pending(void){assert(!xorg_list_is_empty(&priv.copy_releases));return entry(priv.copy_releases.next,struct present_copy_release,link);}
static void done(void){assert(xorg_list_is_empty(&priv.copy_releases));assert(!registered&&!timers);close(pipefds[0]);close(pipefds[1]);}
static void error_case(int mode){
 setup();struct vblank v=frame();present_copy_idle(&v);destroy_vblank(&v);expected_fatal=1;
 struct present_copy_release*r=pending();
 if(mode==0){status=-1;present_copy_release_ready(r->fd,X_NOTIFY_READ,r);}
 if(mode==1){status=0;present_copy_release_ready(r->fd,X_NOTIFY_READ,r);}
 if(mode==2){status=1;present_copy_release_ready(r->fd,X_NOTIFY_ERROR,r);}
 if(mode==3)present_copy_release_timeout(NULL,0,r);
 _exit(88);
}
int main(void){
 setup();struct vblank v=frame();present_copy_idle(&v);
 assert(!idle_events&&!fence_triggers&&!v.idle_fence&&v.pixmap->refcnt==2&&registered==1);
 destroy_vblank(&v);assert(!pixmaps_freed&&!wrappers_freed);
 status=1;struct present_copy_release*r=pending();present_copy_release_ready(r->fd,X_NOTIFY_READ,r);
 assert(idle_events==1&&fence_triggers==1&&wrappers_freed==1&&pixmaps_freed==1);done();
 puts("PASS delayed idle survives Present completion and pixmap resource release");
 setup();v=frame();present_copy_idle(&v);present_copy_release_forget_window(&window);destroy_vblank(&v);
 status=1;r=pending();assert(!r->window);present_copy_release_ready(r->fd,X_NOTIFY_READ,r);
 assert(!idle_events&&fence_triggers==1&&wrappers_freed==1&&pixmaps_freed==1);done();
 puts("PASS destroyed window does not leave a callback window pointer");
 setup();v=frame();present_copy_idle(&v);destroy_vblank(&v);v=frame();present_copy_idle(&v);destroy_vblank(&v);
 present_drain_copy_releases(&screen);assert(finishes==1&&idle_events==2&&pixmaps_freed==2&&wrappers_freed==2);done();
 puts("PASS teardown drains GPU before releasing all resources");
 setup();assert(present_set_copy_release(&screen,NULL,finish));v=frame();present_copy_idle(&v);assert(finishes==1&&idle_events==1&&!registered);destroy_vblank(&v);done();
 setup();priv.copy_finish=NULL;v=frame();present_copy_idle(&v);assert(!finishes&&idle_events==1&&!registered);destroy_vblank(&v);done();
 puts("PASS explicit finish diagnostic and legacy controls");
 setup();v=frame();status=1;assert(write(pipefds[1],"x",1)==1);present_copy_idle(&v);assert(idle_events==1&&!registered);destroy_vblank(&v);done();
 puts("PASS already-ready fence releases exactly once");
 for(int n=0;n<4;n++){pid_t p=fork();assert(p>=0);if(!p)error_case(n);int rc;assert(waitpid(p,&rc,0)==p);assert(WIFEXITED(rc)&&WEXITSTATUS(rc)==86);}
 puts("PASS error, unsignaled, error-readiness and timeout never release idle");
}
'''.replace('CODE',code).replace('#include <stdlib.h>','#include <stdlib.h>\n#include <string.h>')
with tempfile.TemporaryDirectory(prefix='xorg-release-unit-') as tmp:
    f=Path(tmp)/'test.c';f.write_text(c);exe=Path(tmp)/'test'
    subprocess.run([a.cc,'-std=gnu11','-O1','-g','-Wall','-Wextra','-Wno-unused-parameter','-Wno-unused-but-set-variable','-Werror','-fsanitize=address,undefined',str(f),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True,timeout=10)
print(json.dumps({'present_execute_sha256':hashlib.sha256(s.encode()).hexdigest(),'scope':'stubbed lifecycle and fence tests, not hardware validation'}))
