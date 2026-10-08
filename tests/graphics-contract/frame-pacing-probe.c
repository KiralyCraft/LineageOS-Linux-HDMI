/* SPDX-License-Identifier: MIT
 * Diagnostic only: fixed memory records; output is written at process exit.
 * No application/Present options, fences or frame scheduling are changed.
 */
#define _GNU_SOURCE
#include <GL/glx.h>
#include <EGL/egl.h>
#include <X11/extensions/sync.h>
#include <stdio.h>
#include <limits.h>
#include <string.h>
#include <X11/Xlib.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <xcb/present.h>

#define LIMIT 262144u
struct record { uint64_t begin,end; int64_t data[5]; uint32_t event,tid; };
static struct record records[LIMIT];
static _Atomic unsigned committed[LIMIT],used;
static _Atomic int output=-1;
static pid_t owner;
_Static_assert(sizeof(struct record)==64,"trace ABI");
_Static_assert(ATOMIC_INT_LOCK_FREE==2,"signal-safe atomic counters required");
static _Thread_local int viewport_w,viewport_h;
static uint64_t stamp(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void note(unsigned event,uint64_t begin,uint64_t end,int64_t a,int64_t b,int64_t c,int64_t d,int64_t e){
 if(getpid()!=owner || atomic_load_explicit(&output,memory_order_relaxed)<0)return;
 unsigned i=atomic_fetch_add_explicit(&used,1,memory_order_relaxed);if(i>=LIMIT)return;
 records[i]=(struct record){begin,end,{a,b,c,d,e},event,(uint32_t)syscall(SYS_gettid)};
 atomic_store_explicit(&committed[i],1,memory_order_release);
}
static void write_all(int fd,const void *data,size_t size){const char *p=data;while(size){ssize_t n=write(fd,p,size);if(n<0&&errno==EINTR)continue;if(n<=0)break;p+=n;size-=n;}}
static void dump(void){
 int fd=atomic_exchange_explicit(&output,-1,memory_order_relaxed);if(fd<0)return;
 if(getpid()!=owner){close(fd);return;}
 unsigned count=atomic_load_explicit(&used,memory_order_relaxed),limit=count>LIMIT?LIMIT:count;
 uint64_t header[4]={UINT64_C(0x31305046494d4448),64,count,stamp()};
 write_all(fd,header,sizeof(header));
 for(unsigned i=0;i<limit;i++)if(atomic_load_explicit(&committed[i],memory_order_acquire))write_all(fd,&records[i],sizeof(records[i]));
 close(fd);
}
static void stop(int signal){dump();_exit(128+signal);}
__attribute__((constructor)) static void init(void){
 owner=getpid();char name[PATH_MAX];const char *dir=getenv("HDMI_FRAME_TRACE_DIR");
 const char *path=getenv("HDMI_FRAME_TRACE");
 if(dir){int n=snprintf(name,sizeof(name),"%s/trace-%u.bin",dir,(unsigned)owner);if(n<0 || (size_t)n>=sizeof(name))return;path=name;}
 if(!path)return;
 int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(fd<0)return;atomic_store(&output,fd);
 struct sigaction sa={.sa_handler=stop};sigemptyset(&sa.sa_mask);sigaction(SIGTERM,&sa,NULL);
}
__attribute__((destructor)) static void fini(void){dump();}
static void *resolve_symbol(const char *name){
 void *symbol=dlsym(RTLD_NEXT,name);if(symbol)return symbol;
 const char *library=NULL;
 if(!strncmp(name,"xcb_present_",12))library="libxcb-present.so.0";
 else if(!strncmp(name,"xcb_sync_",9))library="libxcb-sync.so.1";
 else if(!strncmp(name,"xcb_",4))library="libxcb.so.1";
 else if(!strncmp(name,"egl",3))library="libEGL.so.1";
 else if(!strncmp(name,"glX",3))library="libGLX.so.0";
 else if(!strcmp(name,"glViewport"))library="libGL.so.1";
 else if(!strncmp(name,"XSync",5))library="libXext.so.6";
 else if(name[0]=='X')library="libX11.so.6";
 if(library){void *handle=dlopen(library,RTLD_LAZY|RTLD_NOLOAD);if(handle){symbol=dlsym(handle,name);dlclose(handle);}}
 if(!symbol && !strcmp(name,"glViewport")){void *handle=dlopen("libGLESv2.so.2",RTLD_LAZY|RTLD_NOLOAD);if(handle){symbol=dlsym(handle,name);dlclose(handle);}}
 if(!symbol)fprintf(stderr,"HDMI diagnostic cannot resolve %s in its loaded library scope\n",name);
 return symbol;
}
#define DECLARE(symbol,ret,args) \
 static ret(*real_##symbol)args; static pthread_once_t once_##symbol=PTHREAD_ONCE_INIT; \
 static void resolve_##symbol(void){real_##symbol=resolve_symbol(#symbol);if(!real_##symbol)_exit(126);}
#define RESOLVE(symbol) pthread_once(&once_##symbol,resolve_##symbol)
DECLARE(glXSwapBuffers,void,(Display*,GLXDrawable))
void glXSwapBuffers(Display*d,GLXDrawable drawable){RESOLVE(glXSwapBuffers);uint64_t b=stamp();real_glXSwapBuffers(d,drawable);note(1,b,stamp(),drawable,viewport_w,viewport_h,0,0);}
typedef struct SDL_Window SDL_Window;
DECLARE(SDL_GL_SwapWindow,void,(SDL_Window*))
void SDL_GL_SwapWindow(SDL_Window*w){RESOLVE(SDL_GL_SwapWindow);uint64_t b=stamp();real_SDL_GL_SwapWindow(w);note(2,b,stamp(),(intptr_t)w,viewport_w,viewport_h,0,0);}
DECLARE(glViewport,void,(GLint,GLint,GLsizei,GLsizei))
void glViewport(GLint x,GLint y,GLsizei w,GLsizei h){RESOLVE(glViewport);viewport_w=w;viewport_h=h;uint64_t b=stamp();real_glViewport(x,y,w,h);note(3,b,stamp(),x,y,w,h,0);}
DECLARE(XNextEvent,int,(Display*,XEvent*))
int XNextEvent(Display*d,XEvent*e){RESOLVE(XNextEvent);int r=real_XNextEvent(d,e);uint64_t t=stamp();if(e->type==ConfigureNotify)note(4,t,t,e->xconfigure.width,e->xconfigure.height,e->xconfigure.serial,0,0);else if(e->type==Expose)note(5,t,t,e->xexpose.width,e->xexpose.height,e->xexpose.count,0,0);return r;}
DECLARE(XPending,int,(Display*))
int XPending(Display*d){RESOLVE(XPending);int r=real_XPending(d);uint64_t t=stamp();note(6,t,t,r,0,0,0,0);return r;}
DECLARE(xcb_present_pixmap,xcb_void_cookie_t,(xcb_connection_t*,xcb_window_t,xcb_pixmap_t,uint32_t,xcb_xfixes_region_t,xcb_xfixes_region_t,int16_t,int16_t,xcb_randr_crtc_t,xcb_sync_fence_t,xcb_sync_fence_t,uint32_t,uint64_t,uint64_t,uint64_t,uint32_t,const xcb_present_notify_t*))
xcb_void_cookie_t xcb_present_pixmap(xcb_connection_t*c,xcb_window_t w,xcb_pixmap_t p,uint32_t s,xcb_xfixes_region_t v,xcb_xfixes_region_t u,int16_t x,int16_t y,xcb_randr_crtc_t crtc,xcb_sync_fence_t wait,xcb_sync_fence_t idle,uint32_t options,uint64_t target,uint64_t div,uint64_t rem,uint32_t n,const xcb_present_notify_t*notify){
 RESOLVE(xcb_present_pixmap);uint64_t b=stamp();xcb_void_cookie_t r=real_xcb_present_pixmap(c,w,p,s,v,u,x,y,crtc,wait,idle,options,target,div,rem,n,notify);note(7,b,stamp(),s,options,target,w,p);return r;
}
static void event(xcb_generic_event_t*e,uint64_t t){
 if(!e||(e->response_type&127)!=XCB_GE_GENERIC)return;
 xcb_present_generic_event_t*g=(void*)e;
 if(g->evtype==XCB_PRESENT_COMPLETE_NOTIFY){xcb_present_complete_notify_event_t*c=(void*)e;note(8,t,t,c->serial,c->kind,c->mode,c->ust,c->msc);}
 else if(g->evtype==XCB_PRESENT_IDLE_NOTIFY){xcb_present_idle_notify_event_t*i=(void*)e;note(9,t,t,i->serial,i->pixmap,i->window,0,0);}
}
DECLARE(xcb_poll_for_special_event,xcb_generic_event_t*,(xcb_connection_t*,xcb_special_event_t*))
xcb_generic_event_t*xcb_poll_for_special_event(xcb_connection_t*c,xcb_special_event_t*s){RESOLVE(xcb_poll_for_special_event);xcb_generic_event_t*e=real_xcb_poll_for_special_event(c,s);event(e,stamp());return e;}
DECLARE(xcb_wait_for_special_event,xcb_generic_event_t*,(xcb_connection_t*,xcb_special_event_t*))
xcb_generic_event_t*xcb_wait_for_special_event(xcb_connection_t*c,xcb_special_event_t*s){RESOLVE(xcb_wait_for_special_event);xcb_generic_event_t*e=real_xcb_wait_for_special_event(c,s);event(e,stamp());return e;}

DECLARE(eglSwapBuffers,EGLBoolean,(EGLDisplay,EGLSurface))
EGLBoolean eglSwapBuffers(EGLDisplay d,EGLSurface surface){RESOLVE(eglSwapBuffers);uint64_t b=stamp();EGLBoolean r=real_eglSwapBuffers(d,surface);note(10,b,stamp(),(intptr_t)d,(intptr_t)surface,viewport_w,viewport_h,r);return r;}
DECLARE(XSyncSetCounter,Status,(Display*,XSyncCounter,XSyncValue))
Status XSyncSetCounter(Display*d,XSyncCounter counter,XSyncValue value){RESOLVE(XSyncSetCounter);uint64_t b=stamp();Status r=real_XSyncSetCounter(d,counter,value);note(11,b,stamp(),counter,value.hi,value.lo,(intptr_t)d,r);return r;}
DECLARE(xcb_sync_set_counter,xcb_void_cookie_t,(xcb_connection_t*,xcb_sync_counter_t,xcb_sync_int64_t))
xcb_void_cookie_t xcb_sync_set_counter(xcb_connection_t*c,xcb_sync_counter_t counter,xcb_sync_int64_t value){RESOLVE(xcb_sync_set_counter);uint64_t b=stamp();xcb_void_cookie_t r=real_xcb_sync_set_counter(c,counter,value);note(12,b,stamp(),counter,value.hi,value.lo,(intptr_t)c,0);return r;}

DECLARE(_exit,void,(int))
void _exit(int status){dump();RESOLVE(_exit);real__exit(status);__builtin_unreachable();}
DECLARE(_Exit,void,(int))
void _Exit(int status){dump();RESOLVE(_Exit);real__Exit(status);__builtin_unreachable();}
