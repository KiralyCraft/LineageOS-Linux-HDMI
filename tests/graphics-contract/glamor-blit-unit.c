#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
typedef int Bool;
typedef int GLint;
typedef unsigned GLenum;
typedef bool GLboolean;
#define TRUE 1
#define FALSE 0
#define X_INFO 1
#define X_WARNING 2
#define GL_READ_FRAMEBUFFER_BINDING 1
#define GL_DRAW_FRAMEBUFFER_BINDING 2
#define GL_READ_FRAMEBUFFER 3
#define GL_DRAW_FRAMEBUFFER 4
#define GL_SCISSOR_TEST 5
#define GL_FRAMEBUFFER_COMPLETE 6
#define GL_COLOR_BUFFER_BIT 7
#define GL_NEAREST 8
#define GL_NO_ERROR 0
#define GL_PIXEL_PACK_BUFFER_BINDING 10
#define GL_PACK_ALIGNMENT 11
#define GL_PACK_ROW_LENGTH 12
#define GL_PACK_SKIP_ROWS 13
#define GL_PACK_SKIP_PIXELS 14
#define GL_PIXEL_PACK_BUFFER 15
#define GL_RGBA 16
#define GL_UNSIGNED_BYTE 17
static int verified, detected_mismatch;
static void LogMessage(int level,const char *format,...) {
 (void)level;char message[256];va_list args;va_start(args,format);vsnprintf(message,sizeof(message),format,args);va_end(args);
 if (strstr(message,"HDMI_E_VERIFY serial=")) {
  verified++;
  if (!strstr(message,"mismatched=0 ")) detected_mismatch++;
 }
}
typedef struct {int fb,tex;bool is_red;} FBO;
typedef struct {FBO *fbo;bool small;} glamor_pixmap_private;
typedef struct {bool has_fbo_blit;} glamor_screen_private;
typedef struct {int x1,y1,x2,y2;} BoxRec,*BoxPtr;
typedef struct {BoxPtr boxes;int count;} RegionRec,*RegionPtr;
struct Pixmap;
typedef struct Screen {struct Pixmap *(*GetScreenPixmap)(struct Screen *);} ScreenRec,*ScreenPtr;
typedef struct Pixmap {struct {ScreenPtr pScreen;int width,height,depth,bitsPerPixel;} drawable;glamor_pixmap_private priv;} PixmapRec,*PixmapPtr;
static PixmapPtr root;
static PixmapPtr get_root(ScreenPtr s){(void)s;return root;}
static glamor_screen_private screen_priv={true};
static glamor_screen_private *glamor_get_screen_private(ScreenPtr s){(void)s;return &screen_priv;}
static glamor_pixmap_private *glamor_get_pixmap_private(PixmapPtr p){return &p->priv;}
static bool glamor_pixmap_priv_is_small(glamor_pixmap_private *p){return p->small;}
struct Format {int internalformat;};
static const struct Format *glamor_format_for_pixmap(PixmapPtr p) {
 static const struct Format rgb={1},other={2};return p->drawable.depth==24?&rgb:&other;
}
#define RegionRects(r) ((r)->boxes)
#define RegionNumRects(r) ((r)->count)
static int read_fbo=71,draw_fbo=72,blit_count,binding_count;
static bool scissor=true,incomplete,fail_blit;
static GLenum gl_error;
static int pack_state[5]={91,8,19,23,37}, finishes;
static bool bad_read;
static void glamor_make_current(glamor_screen_private *p){(void)p;}
static GLenum glGetError(void){GLenum e=gl_error;gl_error=0;return e;}
static void glGetIntegerv(GLenum p,GLint *v){
 if (p>=10 && p<=14) *v=pack_state[p-10];
 else {*v=p==GL_READ_FRAMEBUFFER_BINDING?read_fbo:draw_fbo;}
}
static void glFinish(void){finishes++;}
static void glBindBuffer(GLenum p,GLint v){assert(p==GL_PIXEL_PACK_BUFFER);pack_state[0]=v;}
static void glPixelStorei(GLenum p,GLint v){assert(p>=11&&p<=14);pack_state[p-10]=v;}
static void glReadPixels(int x,int y,int width,int height,GLenum format,GLenum type,void *data){
 assert(x>=0&&y>=0&&width>0&&height>0&&format==GL_RGBA&&type==GL_UNSIGNED_BYTE);
 assert(!pack_state[0]&&pack_state[1]==1&&!pack_state[2]&&!pack_state[3]&&!pack_state[4]);
 memset(data,42,(size_t)width*height*4);
 if (bad_read&&read_fbo==2) ((unsigned char *)data)[0]=99;
}
static GLboolean glIsEnabled(GLenum p){assert(p==GL_SCISSOR_TEST);return scissor;}
static void glDisable(GLenum p){assert(p==GL_SCISSOR_TEST);scissor=false;}
static void glEnable(GLenum p){assert(p==GL_SCISSOR_TEST);scissor=true;}
static void glBindFramebuffer(GLenum p,GLint fb){assert(fb);binding_count++;if(p==GL_READ_FRAMEBUFFER)read_fbo=fb;else {assert(p==GL_DRAW_FRAMEBUFFER);draw_fbo=fb;}}
static GLenum glCheckFramebufferStatus(GLenum p){assert(p==GL_READ_FRAMEBUFFER||p==GL_DRAW_FRAMEBUFFER);return incomplete?0:GL_FRAMEBUFFER_COMPLETE;}
static void glBlitFramebuffer(int x1,int y1,int x2,int y2,int u1,int v1,int u2,int v2,GLenum mask,GLenum filter){
 assert(x1==u1&&x2==u2&&y1==v1&&y2==v2);assert(mask==GL_COLOR_BUFFER_BIT&&filter==GL_NEAREST);blit_count++;if(fail_blit)gl_error=99;
}
/* PRODUCTION */
static void restored(void){
 assert(read_fbo==71&&draw_fbo==72&&scissor);
 assert(pack_state[0]==91&&pack_state[1]==8&&pack_state[2]==19&&pack_state[3]==23&&pack_state[4]==37);
}
int main(int argc,char **argv){
 assert(argc==2);
 const char *mode=argv[1];
 bad_read=!strcmp(mode,"verify-mismatch");
 assert(!setenv("HDMI_LOS_E_DIAGNOSTIC",bad_read?"verify":mode,1));
 assert(!setenv("HDMI_LOS_GLAMOR_COPY","blit",1));ScreenRec screen={get_root};
 FBO a={1,11,false},b={2,12,false};
 PixmapRec src={{&screen,800,600,24,32},{&a,true}},dst={{&screen,800,600,24,32},{&b,true}};
 root=&src;BoxRec box={0,0,800,600};RegionRec damage={&box,1};
 assert(glamor_copy_tearfree(&src,&dst,&damage));assert(blit_count==1);restored();
 assert(finishes==(!strcmp(mode,"finish-before")||!strcmp(mode,"finish-after")));
 assert(verified==(!strcmp(mode,"verify")||bad_read));
 assert(detected_mismatch==bad_read);
 a.fb=0;int bindings=binding_count;assert(!glamor_copy_tearfree(&src,&dst,&damage));assert(binding_count==bindings);a.fb=1;
 b.fb=0;assert(!glamor_copy_tearfree(&src,&dst,&damage));assert(binding_count==bindings);b.fb=2;
 root=&dst;assert(!glamor_copy_tearfree(&src,&dst,&damage));root=&src;
 b.tex=a.tex;assert(!glamor_copy_tearfree(&src,&dst,&damage));b.tex=12;
 dst.drawable.width=799;assert(!glamor_copy_tearfree(&src,&dst,&damage));dst.drawable.width=800;
 box.x1=-1;assert(!glamor_copy_tearfree(&src,&dst,&damage));box.x1=0;
 incomplete=true;assert(!glamor_copy_tearfree(&src,&dst,&damage));restored();assert(blit_count==1);incomplete=false;
 fail_blit=true;assert(!glamor_copy_tearfree(&src,&dst,&damage));restored();assert(blit_count==2);fail_blit=false;
 gl_error=88;assert(!glamor_copy_tearfree(&src,&dst,&damage));restored();assert(blit_count==2);
 scissor=false;assert(glamor_copy_tearfree(&src,&dst,&damage));assert(!scissor&&read_fbo==71&&draw_fbo==72);
 printf("PASS: production TearFree blit scope, errors, diagnostic %s, RGB comparison and state restoration\n",mode);
}
