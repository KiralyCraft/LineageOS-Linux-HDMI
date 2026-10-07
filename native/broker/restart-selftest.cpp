// Host fixture for real pause/release, composer acquire guards and stop IDs.
// Run unprivileged: fake socket peers only, no Android/DRM devices.
#include <algorithm>
#include <atomic>
#include <cmath>
#include <string>
#include <vector>
#include <assert.h>
#include <sys/wait.h>
#include "../common/hdmi_connected_restart.h"
#include "../common/hdmi_timing_session.h"
#define private public
#define main broker_program_main
#include "main.cpp"
#undef main
#undef private

static hdmi_los_message mode() {
  auto m = make_message(HDMI_LOS_OP_STATUS);
  m.version = HDMI_LOS_VERSION;
  m.flags = HDMI_LOS_FLAG_CONNECTED | HDMI_LOS_FLAG_ACTIVE_MODE | HDMI_LOS_FLAG_LEASE_READY;
  m.active_width = 3840; m.active_height = 2160; m.active_refresh_millihz = 30000;
  return m;
}
static void child_ok(pid_t pid) {
  int status; assert(waitpid(pid,&status,0)==pid);
  assert(WIFEXITED(status) && WEXITSTATUS(status)==0);
}
static void pause_test(bool acknowledge) {
  int composer[2],agent[2];
  assert(socketpair(AF_UNIX,SOCK_SEQPACKET,0,composer)==0);
  assert(socketpair(AF_UNIX,SOCK_STREAM,0,agent)==0);
  pid_t child=fork();assert(child>=0);
  if(!child) {
    close(composer[0]);close(agent[0]);
    hdmi_los_message request;int fd=-1;
    assert(recv_with_fd(composer[1],&request,&fd));
    assert(request.opcode==HDMI_LOS_OP_STATUS && fd<0);
    auto reply=mode();reply.opcode=request.opcode|HDMI_LOS_OP_RESPONSE;
    reply.request_id=request.request_id;reply.state=HDMI_LOS_STATE_LEASED;
    assert(send_with_fd(composer[1],reply,-1));
    assert(read_full(agent[1],&request,sizeof(request)));
    assert(request.opcode==HDMI_LOS_OP_AGENT_STOP);
    reply=make_message(HDMI_LOS_OP_AGENT_READY);reply.request_id=request.request_id;
    if(acknowledge)assert(write_full(agent[1],&reply,sizeof(reply)));
    else close(agent[1]);
    assert(recv_with_fd(composer[1],&request,&fd));assert(request.opcode==HDMI_LOS_OP_RELEASE);
    reply=mode();reply.opcode=request.opcode|HDMI_LOS_OP_RESPONSE;
    reply.request_id=request.request_id;reply.state=HDMI_LOS_STATE_ANDROID;
    assert(send_with_fd(composer[1],reply,-1));
    _exit(0);
  }
  close(composer[1]);close(agent[1]);
  Broker b;b.composer_fd_=composer[0];b.agent_fd_=agent[0];b.active_=true;
  b.armed_=true;b.preference_applied_=true;
  std::string detail;int result=b.PauseConnected(&detail);
  assert(result==(acknowledge ? HDMI_LOS_OK : HDMI_LOS_ERR_IO));
  assert(!b.active_ && !b.armed_ && b.volumes_.down<0 && !b.timing_.generation());
  assert(b.restart_.ready()==acknowledge);
  assert(b.preference_applied_==acknowledge);
  if(acknowledge){assert(b.PauseConnected(&detail)==HDMI_LOS_OK);}
  close(composer[0]);close(agent[0]);child_ok(child);
}
static void acquire_guard_test(bool changed,bool unplug_replug) {
  int sockets[2];assert(socketpair(AF_UNIX,SOCK_SEQPACKET,0,sockets)==0);
  pid_t child=fork();assert(child>=0);
  if(!child){
    close(sockets[0]);hdmi_los_message request;int fd=-1;
    assert(recv_with_fd(sockets[1],&request,&fd));
    auto reply=mode();
    if(unplug_replug){
      auto event=reply;event.opcode=HDMI_LOS_OP_HOTPLUG;event.request_id=0;event.flags=0;
      assert(send_with_fd(sockets[1],event,-1));
      event.flags=reply.flags;assert(send_with_fd(sockets[1],event,-1));
    }
    if(changed)reply.active_refresh_millihz=60000;
    reply.opcode=request.opcode|HDMI_LOS_OP_RESPONSE;reply.request_id=request.request_id;
    assert(send_with_fd(sockets[1],reply,-1));_exit(0);
  }
  close(sockets[1]);Broker b;b.composer_fd_=sockets[0];b.connected_start_=true;
  assert(b.restart_.Remember(mode(),monotonic_ms()));
  hdmi_los_message reply={};
  assert(b.ComposerRequest(HDMI_LOS_OP_ACQUIRE,&reply,nullptr,HDMI_LOS_ACQUIRE_PREPARE));
  assert(reply.status==((changed||unplug_replug)?HDMI_LOS_ERR_STATE:HDMI_LOS_OK));
  close(sockets[0]);child_ok(child);
}
static void resume_gate_test(bool changed) {
  int sockets[2];assert(socketpair(AF_UNIX,SOCK_SEQPACKET,0,sockets)==0);
  pid_t child=fork();assert(child>=0);
  if(!child){
    close(sockets[0]);
    for(unsigned i=0;i<(changed?1u:3u);i++){
      hdmi_los_message request;int fd=-1;
      assert(recv_with_fd(sockets[1],&request,&fd));assert(request.opcode==HDMI_LOS_OP_STATUS);
      auto reply=mode();if(changed)reply.active_height=1080;
      reply.opcode=request.opcode|HDMI_LOS_OP_RESPONSE;reply.request_id=request.request_id;
      assert(send_with_fd(sockets[1],reply,-1));
    }
    _exit(0);
  }
  close(sockets[1]);Broker b;b.composer_fd_=sockets[0];b.agent_fd_=123456;
  assert(b.restart_.Remember(mode(),monotonic_ms()));
  std::string detail;int result=b.ResumeConnected(&detail);
  // Matching stable status proceeds to the unchanged production startup gate.
  // This unprivileged host lacks the Android compatibility marker; it must
  // cancel cleanly before attempting any real input/display operation.
  assert(result==(changed?HDMI_LOS_ERR_STATE:HDMI_LOS_ERR_INCOMPATIBLE));
  assert(!b.restart_.ready() && !b.active_ && !b.armed_);
  close(sockets[0]);child_ok(child);
}
int main(){
  assert(getuid()!=0 && "Run host fixture unprivileged");
  HdmiConnectedRestart token;auto status=mode();
  assert(token.Remember(status,100));assert(token.Matches(status));
  assert(!token.Expired(300099));assert(token.Expired(300100));
  status.active_refresh_millihz++;assert(!token.Matches(status));
  token.Cancel();assert(!token.Matches(mode()));
  status=mode();status.flags&=~HDMI_LOS_FLAG_CONNECTED;assert(!token.Remember(status,0));
  pause_test(true);pause_test(false);
  acquire_guard_test(false,false);acquire_guard_test(true,false);acquire_guard_test(false,true);
  resume_gate_test(true);resume_gate_test(false);
  {
    Broker waiting;std::string detail;
    assert(waiting.ResumeConnected(&detail)==HDMI_LOS_ERR_STATE);
    assert(waiting.restart_.Remember(mode(),monotonic_ms()));
    assert(waiting.ResumeConnected(&detail)==HDMI_LOS_ERR_AGENT && waiting.restart_.ready());
    waiting.restart_.Cancel();
    assert(waiting.restart_.Remember(mode(),monotonic_ms()-HdmiConnectedRestart::kLifetimeMs));
    assert(waiting.ResumeConnected(&detail)==HDMI_LOS_ERR_STATE && !waiting.restart_.ready());
  }
  int pair[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,pair)==0);
  Broker b;b.agent_fd_=pair[0];auto stale=make_message(HDMI_LOS_OP_AGENT_READY);
  stale.request_id=41;assert(write_full(pair[1],&stale,sizeof(stale)));
  hdmi_los_message reply={};assert(!b.WaitAgent(HDMI_LOS_OP_AGENT_READY,20,&reply,true,42));
  stale.request_id=42;assert(write_full(pair[1],&stale,sizeof(stale)));
  assert(b.WaitAgent(HDMI_LOS_OP_AGENT_READY,20,&reply,true,42));
  close(pair[0]);close(pair[1]);
  puts("PASS: connected pause, failed stop, mode/expiry guards, unplug-replug cancellation, correlated stop acknowledgement");
}
