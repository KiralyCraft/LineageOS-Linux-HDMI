#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
#define LogMessage(...) ((void)0)
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
static struct {int internalformat;} format={1};
#define glamor_format_for_pixmap(p) (&format)
#define RegionRects(r) ((r)->boxes)
#define RegionNumRects(r) ((r)->count)
static int read_fbo=71,draw_fbo=72,blit_count,binding_count;
static bool scissor=true,incomplete,fail_blit;
static GLenum gl_error;
static void glamor_make_current(glamor_screen_private *p){(void)p;}
static GLenum glGetError(void){GLenum e=gl_error;gl_error=0;return e;}
static void glGetIntegerv(GLenum p,GLint *v){*v=p==GL_READ_FRAMEBUFFER_BINDING?read_fbo:draw_fbo;}
static GLboolean glIsEnabled(GLenum p){assert(p==GL_SCISSOR_TEST);return scissor;}
static void glDisable(GLenum p){assert(p==GL_SCISSOR_TEST);scissor=false;}
static void glEnable(GLenum p){assert(p==GL_SCISSOR_TEST);scissor=true;}
static void glBindFramebuffer(GLenum p,GLint fb){assert(fb);binding_count++;if(p==GL_READ_FRAMEBUFFER)read_fbo=fb;else {assert(p==GL_DRAW_FRAMEBUFFER);draw_fbo=fb;}}
static GLenum glCheckFramebufferStatus(GLenum p){assert(p==GL_READ_FRAMEBUFFER||p==GL_DRAW_FRAMEBUFFER);return incomplete?0:GL_FRAMEBUFFER_COMPLETE;}
static void glBlitFramebuffer(int x1,int y1,int x2,int y2,int u1,int v1,int u2,int v2,GLenum mask,GLenum filter){
 assert(x1==u1&&x2==u2&&y1==v1&&y2==v2);assert(mask==GL_COLOR_BUFFER_BIT&&filter==GL_NEAREST);blit_count++;if(fail_blit)gl_error=99;
}
/* PRODUCTION */
static void restored(void){assert(read_fbo==71&&draw_fbo==72&&scissor);}
int main(void){
 assert(!setenv("HDMI_LOS_GLAMOR_COPY","blit",1));ScreenRec screen={get_root};
 FBO a={1,11,false},b={2,12,false};
 PixmapRec src={{&screen,800,600,24,32},{&a,true}},dst={{&screen,800,600,24,32},{&b,true}};
 root=&src;BoxRec box={0,0,800,600};RegionRec damage={&box,1};
 assert(glamor_copy_tearfree(&src,&dst,&damage));assert(blit_count==1);restored();
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
 puts("PASS: production TearFree blit scope, zero FBO, geometry, alias rejection, completeness, GL errors and state restoration");
}
