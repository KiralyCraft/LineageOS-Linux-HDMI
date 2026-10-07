#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <linux/sync_file.h>
typedef int Bool;typedef uint32_t CARD32;typedef unsigned XID;
#define TRUE 1
#define FALSE 0
#define Success 0
#define BadAlloc 11
#define X_NOTIFY_NONE 0
#define X_NOTIFY_READ 1
#define X_NOTIFY_ERROR 4
#define ClientStateGone 3
#define ClientStateRetained 4
struct xorg_list { struct xorg_list *next,*prev; };
static void xorg_list_init(struct xorg_list *l){l->next=l->prev=l;}
static void xorg_list_append(struct xorg_list *l,struct xorg_list *h){l->prev=h->prev;l->next=h;h->prev->next=l;h->prev=l;}
static void xorg_list_del(struct xorg_list *l){l->prev->next=l->next;l->next->prev=l->prev;}
#define entry(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
#define xorg_list_for_each_entry_safe(p,n,h,m) \
 for(p=entry((h)->next,__typeof__(*p),m),n=entry(p->m.next,__typeof__(*p),m);&p->m!=(h);p=n,n=entry(n->m.next,__typeof__(*n),m))
typedef struct Client {int clientGone,clientState,ignored,delivered,errors,bytes;unsigned char reply[64];} *ClientPtr;
typedef struct Timer *OsTimerPtr;typedef void *CallbackListPtr;
typedef struct Priv *dri3_screen_priv_ptr;
typedef struct Screen {dri3_screen_priv_ptr priv;} *ScreenPtr;
typedef struct Pixmap {struct {ScreenPtr pScreen;XID id;} drawable;} *PixmapPtr;
typedef struct {ClientPtr client;} NewClientInfoRec;
struct Priv {struct xorg_list fd_exports;int(*fd_export_fence)(ScreenPtr);unsigned fd_export_pending;OsTimerPtr fd_export_timer;Bool fd_export_timer_running,fd_export_callback_registered;};
static dri3_screen_priv_ptr dri3_screen_priv(ScreenPtr s){return s->priv;}
struct Timer {CARD32(*fn)(OsTimerPtr,CARD32,void*);void *data;int active;};
static CallbackListPtr ClientStateCallback;
static void(*state_callback)(CallbackListPtr*,void*,void*);static void *state_data;
static int fail_notify,fail_timer,fail_callback,fail_write=-1,ioctl_error,native_status=1,export_fd=-1;static CARD32 now=100;
static struct {void(*fn)(int,int,void*);void *data;} notices[4096];
static Bool AddCallback(CallbackListPtr*l,void(*f)(CallbackListPtr*,void*,void*),void*d){(void)l;if(fail_callback)return FALSE;state_callback=f;state_data=d;return TRUE;}
static void DeleteCallback(CallbackListPtr*l,void(*f)(CallbackListPtr*,void*,void*),void*d){(void)l;assert(state_callback==f&&state_data==d);state_callback=NULL;state_data=NULL;}
static Bool SetNotifyFd(int fd,void(*f)(int,int,void*),int mask,void*d){assert(fd>=0&&fd<4096);if(mask&&fail_notify)return FALSE;notices[fd].fn=f;notices[fd].data=d;return TRUE;}
static void RemoveNotifyFd(int fd){SetNotifyFd(fd,NULL,0,NULL);}
static OsTimerPtr TimerSet(OsTimerPtr t,int flags,int ms,CARD32(*f)(OsTimerPtr,CARD32,void*),void*d){assert(!flags&&ms==250);if(fail_timer)return NULL;if(!t)t=calloc(1,sizeof(*t));assert(t);t->fn=f;t->data=d;t->active=1;return t;}
static void TimerCancel(OsTimerPtr t){assert(t);t->active=0;}
static void TimerFree(OsTimerPtr t){free(t);}
static CARD32 GetTimeInMillis(void){return now;}
static void IgnoreClient(ClientPtr c){assert(!c->ignored);c->ignored=1;}
static void AttendClient(ClientPtr c){assert(c->ignored);c->ignored=0;}
static int WriteFdToClient(ClientPtr c,int fd,Bool consume){assert(consume);if(c->delivered==fail_write)return -1;c->delivered++;assert(close(fd)==0);return 0;}
static void WriteToClient(ClientPtr c,unsigned bytes,const void *p){assert(bytes<=64);memcpy(c->reply,p,bytes);c->bytes=bytes;}
static void SendErrorToClient(ClientPtr c,unsigned major,unsigned minor,XID resource,int error){assert(major==77&&minor==3&&resource==42&&error==BadAlloc);c->errors++;}
static int fake_ioctl(int fd,unsigned long request,void *data){(void)fd;assert(request==SYNC_IOC_FILE_INFO);if(ioctl_error){errno=EINVAL;return -1;}((struct sync_file_info*)data)->status=native_status;return 0;}
#define ioctl fake_ioctl
/* PRODUCTION */
static int export_fence(ScreenPtr s){(void)s;int fd=export_fd;export_fd=-1;return fd;}
static int buffer_fd(void){int fd=open("/dev/null",O_RDONLY|O_CLOEXEC);assert(fd>=0);return fd;}
static int fence(bool ready,int *writer){int p[2];assert(pipe2(p,O_CLOEXEC)==0);*writer=p[1];if(ready)assert(write(*writer,"x",1)==1);return p[0];}
static void closed(int fd){errno=0;assert(fcntl(fd,F_GETFD)==-1&&errno==EBADF);}
static void init(struct Priv*p,struct Screen*s){memset(p,0,sizeof(*p));xorg_list_init(&p->fd_exports);s->priv=p;assert(dri3_set_fd_export_fence(s,export_fence));}
static int request(struct Screen*s,struct Client*c,int *fds,int n,unsigned char*reply){struct Pixmap pix={.drawable={s,42}};return dri3_send_pixmap_export_reply(c,&pix,77,3,fds,n,reply,32);}
static void fire(int fd){assert(notices[fd].fn);notices[fd].fn(fd,X_NOTIFY_READ,notices[fd].data);}
int main(void){
 struct Priv p;struct Screen s;struct Client c={0};unsigned char reply[32];memset(reply,0x5a,sizeof(reply));int w,fd;
 init(&p,&s);export_fd=fence(false,&w);int native=export_fd;fd=buffer_fd();assert(request(&s,&c,&fd,1,reply)==Success);assert(c.ignored&&!c.delivered&&p.fd_export_pending==1);
 memset(reply,0,sizeof(reply));assert(write(w,"x",1)==1);fire(native);assert(!c.ignored&&c.delivered==1&&c.bytes==32&&!c.errors&&!p.fd_export_pending&&c.reply[0]==0x5a);closed(fd);closed(native);close(w);dri3_stop_fd_exports(&p);
 // An already completed producer takes the immediate path.
 memset(&c,0,sizeof(c));init(&p,&s);export_fd=fence(true,&w);native=export_fd;fd=buffer_fd();assert(request(&s,&c,&fd,1,reply)==Success&&!c.ignored&&c.delivered==1);closed(fd);closed(native);close(w);dri3_stop_fd_exports(&p);
 // Error-completed fences never disclose storage or claim successful readiness.
 memset(&c,0,sizeof(c));init(&p,&s);native_status=-1;export_fd=fence(true,&w);fd=buffer_fd();assert(request(&s,&c,&fd,1,reply)==BadAlloc&&!c.delivered);closed(fd);close(w);native_status=1;dri3_stop_fd_exports(&p);
 // Disconnect, retained resources, timeout, and screen teardown each cancel a pending export.
 for(int reason=0;reason<4;reason++){memset(&c,0,sizeof(c));init(&p,&s);now=UINT32_MAX-10;export_fd=fence(false,&w);native=export_fd;fd=buffer_fd();assert(request(&s,&c,&fd,1,reply)==Success);
  if(reason<2){c.clientState=reason?ClientStateRetained:ClientStateGone;c.clientGone=1;NewClientInfoRec info={&c};state_callback(&ClientStateCallback,state_data,&info);assert(!c.delivered&&!c.errors);}
  else if(reason==2){now+=5001;assert(p.fd_export_timer->fn(p.fd_export_timer,now,p.fd_export_timer->data)==0);assert(c.errors==1&&!c.ignored);}
  else{dri3_stop_fd_exports(&p);assert(c.errors==1&&!c.ignored);}
  assert(!p.fd_export_pending&&!notices[native].fn);closed(fd);closed(native);close(w);if(reason!=3)dri3_stop_fd_exports(&p);
 }
 // Setup failures preserve the current client's dispatch and release every FD.
 for(int reason=0;reason<3;reason++){memset(&c,0,sizeof(c));init(&p,&s);export_fd=reason==2?-1:fence(false,&w);native=export_fd;fd=buffer_fd();fail_notify=reason==0;fail_timer=reason==1;assert(request(&s,&c,&fd,1,reply)==BadAlloc&&!c.ignored&&!c.delivered&&!p.fd_export_pending);closed(fd);if(native>=0){closed(native);close(w);}fail_notify=fail_timer=0;dri3_stop_fd_exports(&p);}
 // FD queue failure transfers earlier FDs exactly once and closes all unpassed FDs.
 memset(&c,0,sizeof(c));init(&p,&s);export_fd=fence(true,&w);int fds[4];for(int i=0;i<4;i++)fds[i]=buffer_fd();int saved[4];memcpy(saved,fds,sizeof(fds));fail_write=1;assert(request(&s,&c,fds,4,reply)==BadAlloc&&c.delivered==1&&!c.bytes);for(int i=0;i<4;i++)closed(saved[i]);close(w);fail_write=-1;dri3_stop_fd_exports(&p);
 puts("PASS: production deferred export reply; payload snapshot, immediate readiness, negative fence, client loss, timeout wrap, screen teardown, setup failures and FD ownership");
}
