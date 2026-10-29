// Native test harness (batch scripts and live mode).
//
// Batch mode:  sim < script   -> parse script, warm up, enter seccomp strict, run, print snapshots.
// Live mode:   sim --live     -> same, but command lines (SET / RUN / END) are read one at a time
//                                after seccomp is on; user_setup() runs at the first RUN.
//
// Script grammar (one command per line): CASE <id> | SET <channel> <value> | RUN <ms> | END.
// RUN <T> executes every tick with t < T (ticks are multiples of FL_TICK_MS starting at 0), so a
// SET issued after RUN <T> is visible to the tick at t = T.
//
// Everything is statically allocated. After seccomp only read/write/_exit/sigreturn may be used.
// If strict mode is refused (already inside a filtered container) an equivalent BPF allowlist is used.
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "FirmLoop.h"
#include "FirmLoopInternal.h"
#include "fake_hal.h"
#include "fl_json.h"
#include "fl_value.h"

namespace {

constexpr size_t SCRIPT_CAP = 64 * 1024;
constexpr int MAX_OPS = 512;
#ifndef FL_OUT_CAP
#define FL_OUT_CAP 2048  // tests override this to force a snapshot overflow
#endif
constexpr size_t OUT_CAP = FL_OUT_CAP;
constexpr size_t NAME_CAP = 32;
constexpr size_t VAL_CAP = 32;

enum class OpType : uint8_t { Set, Run, End };

struct Op {
  OpType type;
  uint32_t t;
  char ch[NAME_CAP];
  char val[VAL_CAP];
};

char g_script[SCRIPT_CAP + 1];
Op g_ops[MAX_OPS];
char g_out[OUT_CAP];

bool g_setup_done = false;
uint32_t g_next_tick = 0;

bool writeAll(const char* p, size_t n) {
  while (n) {
    ssize_t w = write(1, p, n);
    if (w <= 0) return false;
    p += w;
    n -= (size_t)w;
  }
  return true;
}

void writeLine(const char* s) {
  writeAll(s, strlen(s));
  writeAll("\n", 1);
}

[[noreturn]] void finish(int code) { syscall(SYS_exit, code); for (;;) {} }

// Parses one line (no trailing newline). Returns 0 = blank/ignored, 1 = op filled, -1 = error.
int parseLine(char* line, Op* op) {
  while (*line == ' ' || *line == '\t' || *line == '\r') ++line;
  if (!*line) return 0;
  char* cmd = line;
  while (*line && *line != ' ' && *line != '\t' && *line != '\r') ++line;
  char* arg = line;
  while (*arg == ' ' || *arg == '\t') ++arg;
  if (*line) *line = 0;
  // trim trailing whitespace of args
  size_t al = strlen(arg);
  while (al && (arg[al - 1] == ' ' || arg[al - 1] == '\t' || arg[al - 1] == '\r')) arg[--al] = 0;

  if (strcmp(cmd, "CASE") == 0) return 0;
  if (strcmp(cmd, "END") == 0) { op->type = OpType::End; return 1; }
  if (strcmp(cmd, "RUN") == 0) {
    char* end = nullptr;
    long v = strtol(arg, &end, 10);
    if (end == arg || *end || v < 0) return -1;
    op->type = OpType::Run;
    op->t = (uint32_t)v;
    return 1;
  }
  if (strcmp(cmd, "SET") == 0) {
    char* sp = arg;
    while (*sp && *sp != ' ' && *sp != '\t') ++sp;
    if (!*sp || sp == arg) return -1;
    *sp++ = 0;
    while (*sp == ' ' || *sp == '\t') ++sp;
    if (!*sp || strlen(arg) >= NAME_CAP || strlen(sp) >= VAL_CAP) return -1;
    op->type = OpType::Set;
    strcpy(op->ch, arg);
    strcpy(op->val, sp);
    return 1;
  }
  return -1;
}

void snapshot(uint32_t t) {
  fl::JsonWriter w(g_out, sizeof g_out);
  w.beginObj();
  w.key("t"); w.u32(t);
  fl::writeState(w, fakehal::View());
  w.key("err"); w.beginArr();
  for (int e = 0; e < fakehal::errorCount(); ++e) w.str(fakehal::errorText(e));
  w.endArr();
  w.endObj();
  if (w.overflow()) {
    writeLine("{\"fatal\":\"snapshot overflow\"}");
    finish(2);
  }
  writeLine(w.c_str());
}

void ensureSetup() {
  if (g_setup_done) return;
  g_setup_done = true;
  fakehal::setNow(0);
  user_setup();
}

void exec(const Op& op) {
  switch (op.type) {
    case OpType::Set:
      fakehal::setSensor(op.ch, op.val);
      break;
    case OpType::Run:
      ensureSetup();
      while (g_next_tick < op.t) {
        fakehal::setNow(g_next_tick);
        user_tick(g_next_tick);
        snapshot(g_next_tick);
        fakehal::clearErrors();
        g_next_tick += FL_TICK_MS;
      }
      break;
    case OpType::End:
      ensureSetup();
      break;
  }
}

// Touch every libc path user code may use so no lazy init / allocation / syscall happens later.
void warmUp() {
  char s[64];
  snprintf(s, sizeof s, "%d %u %.1f %.2f %s %.*f", -1, 2u, 3.25f, 4.5f, "x", 1, 5.5f);
  volatile float f = 1.5f;
  volatile float sink = fabsf(-f) + roundf(f) + floorf(f) + ceilf(f) + fminf(f, 2.0f) +
                        fmaxf(f, 2.0f);
  (void)sink;
  volatile int c = strcmp("a", "b") + strncmp("a", "b", 1);
  (void)c;
  char tmp[8];
  strncpy(tmp, "abc", sizeof tmp);
  fl::JsonWriter w(g_out, sizeof g_out);
  w.beginObj(); w.key("k"); w.f32(1.0f, 1); w.str("v"); w.endObj();
  fakehal::reset();
  fakehal::setSensor("temp", "1.0");  // exercises strtof; result discarded by reset below
  fakehal::reset();
}

#if defined(__x86_64__)
constexpr uint32_t SECCOMP_ARCH = AUDIT_ARCH_X86_64;
#elif defined(__aarch64__)
constexpr uint32_t SECCOMP_ARCH = AUDIT_ARCH_AARCH64;
#else
#error "unsupported architecture for the seccomp fallback filter"
#endif

// Fallback when SECCOMP_MODE_STRICT is refused: a process that already runs under a seccomp
// filter (every Docker container with the default profile) cannot switch to strict mode (EINVAL).
// Filters stack, so we add one allowing exactly what strict mode allows (read, write, exit,
// rt_sigreturn) and killing the process on anything else (it dies by SIGSYS instead of SIGKILL).
bool installFilterFallback() {
  const uint32_t nr_off = offsetof(struct seccomp_data, nr);
  const uint32_t arch_off = offsetof(struct seccomp_data, arch);
  struct sock_filter prog[] = {
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, arch_off),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SECCOMP_ARCH, 1, 0),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, nr_off),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_read, 4, 0),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_write, 3, 0),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_exit, 2, 0),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_rt_sigreturn, 1, 0),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
  };
  struct sock_fprog fprog = {(unsigned short)(sizeof prog / sizeof prog[0]), prog};
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) return false;
  return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &fprog) == 0;
}

void reportMode(const char* line) { (void)!write(2, line, strlen(line)); }

void enterSeccomp() {
  if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_STRICT) == 0) { reportMode("seccomp=strict\n"); return; }
  if (errno == EINVAL && installFilterFallback()) { reportMode("seccomp=filter\n"); return; }
  writeLine("{\"fatal\":\"seccomp unavailable\"}");
  exit(3);
}

int runBatch() {
  size_t n = 0;
  for (;;) {
    ssize_t r = read(0, g_script + n, SCRIPT_CAP - n);
    if (r < 0) { writeLine("{\"fatal\":\"read error\"}"); return 2; }
    if (r == 0) break;
    n += (size_t)r;
    if (n >= SCRIPT_CAP) { writeLine("{\"fatal\":\"script too large\"}"); return 2; }
  }
  g_script[n] = 0;

  int nops = 0, lineno = 0;
  char* p = g_script;
  while (*p) {
    char* nl = strchr(p, '\n');
    if (nl) *nl = 0;
    ++lineno;
    if (nops >= MAX_OPS) { writeLine("{\"fatal\":\"script too long\"}"); return 2; }
    int rc = parseLine(p, &g_ops[nops]);
    if (rc < 0) {
      snprintf(g_out, sizeof g_out, "{\"fatal\":\"bad script line %d\"}", lineno);
      writeLine(g_out);
      return 2;
    }
    if (rc > 0) ++nops;
    if (!nl) break;
    p = nl + 1;
  }

  warmUp();
  fakehal::reset();
  enterSeccomp();
  for (int i = 0; i < nops; ++i) exec(g_ops[i]);
  ensureSetup();
  writeLine("{\"done\":true}");
  finish(0);
}

int runLive() {
  warmUp();
  fakehal::reset();
  enterSeccomp();
  size_t len = 0;
  bool eof = false;
  for (;;) {
    // consume complete lines
    char* nl = (char*)memchr(g_script, '\n', len);
    if (!nl && !eof) {
      if (len >= SCRIPT_CAP) { writeLine("{\"fatal\":\"line too long\"}"); finish(2); }
      ssize_t r = read(0, g_script + len, SCRIPT_CAP - len);
      if (r <= 0) eof = true; else len += (size_t)r;
      continue;
    }
    if (!nl && len == 0) break;
    size_t linelen = nl ? (size_t)(nl - g_script) : len;
    g_script[linelen] = 0;
    Op op;
    int rc = parseLine(g_script, &op);
    size_t consumed = nl ? linelen + 1 : len;
    memmove(g_script, g_script + consumed, len - consumed);
    len -= consumed;
    if (rc < 0) { writeLine("{\"fatal\":\"bad command\"}"); finish(2); }
    if (rc > 0) {
      exec(op);
      if (op.type == OpType::End) break;
    }
  }
  ensureSetup();
  writeLine("{\"done\":true}");
  finish(0);
}

}  // namespace

int main(int argc, char** argv) {
  bool live = argc > 1 && strcmp(argv[1], "--live") == 0;
  int rc = live ? runLive() : runBatch();
  return rc;
}
