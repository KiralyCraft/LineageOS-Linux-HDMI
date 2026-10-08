/* SPDX-License-Identifier: MIT
 * Real XCB event routing: queued-only general drain must leave new socket data
 * readable so a special-event worker cannot miss its own wakeup.
 */
#include <assert.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <xcb/xcb.h>
#include <xcb/present.h>
int main(void) {
 xcb_connection_t *c=xcb_connect(NULL,NULL);assert(!xcb_connection_has_error(c));
 xcb_screen_t *screen=xcb_setup_roots_iterator(xcb_get_setup(c)).data;
 xcb_window_t w=xcb_generate_id(c);uint32_t value=1;
 xcb_create_window(c,screen->root_depth,w,screen->root,0,0,8,8,0,XCB_WINDOW_CLASS_INPUT_OUTPUT,screen->root_visual,XCB_CW_OVERRIDE_REDIRECT,&value);
 xcb_present_event_t id=xcb_generate_id(c);uint32_t stamp=0;
 xcb_generic_error_t *err=xcb_request_check(c,xcb_present_select_input_checked(c,id,w,XCB_PRESENT_EVENT_MASK_COMPLETE_NOTIFY));assert(!err);
 xcb_special_event_t *special=xcb_register_for_special_xge(c,&xcb_present_id,id,&stamp);assert(special);
 for(int control=0;control<2;control++) {
  assert(!xcb_poll_for_special_event(c,special));
  xcb_present_notify_msc(c,w,control+1,0,0,0);xcb_flush(c);
  struct pollfd fd={xcb_get_file_descriptor(c),POLLIN,0};assert(poll(&fd,1,2000)==1);
  xcb_generic_event_t *general=control?xcb_poll_for_queued_event(c):xcb_poll_for_event(c);assert(!general);
  fd.revents=0;int readable=poll(&fd,1,0);
  xcb_generic_event_t *complete=xcb_poll_for_special_event(c,special);assert(complete);
  xcb_present_complete_notify_event_t *event=(void*)complete;assert(event->serial==(unsigned)control+1);free(complete);
  if(control)assert(readable==1);else assert(readable==0);
  printf("%s: %s\n",control?"queued-only drain preserves readiness":"general socket poll strands the special event",control?"PASS":"REPRODUCED");
 }
 xcb_unregister_for_special_event(c,special);xcb_destroy_window(c,w);xcb_disconnect(c);
 return 0;
}
